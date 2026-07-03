"""
Aggregates data for appliances
Written by Ian David Elder for the CANOE model
"""

import canoe_residential.utils as utils
import canoe_residential.nrcan as nrcan
import pandas as pd
import os
import sqlite3
from canoe_schema.v4_0 import models as schema_models
from canoe_residential.common import ResidentialRuntime


def aggregate(runtime: ResidentialRuntime, conn: sqlite3.Connection):

    for region in runtime.cfg.province_list: aggregate_region(region, runtime, conn)

    print(f"Appliances data aggregated into {os.path.basename(runtime.cfg.db_dir)}\n")



def aggregate_region(region: str, runtime: ResidentialRuntime, conn: sqlite3.Connection):

    curs = conn.cursor() # Cursor object interacts with the sqlite db

    exs_techs = runtime.existing_techs
    new_techs = runtime.new_techs
    fuel_commodities = runtime.fuel_commodities
    end_use_demands = runtime.end_use_demands
    aeo_res_class = runtime.aeo_res_class
    aeo_res_equip = runtime.aeo_res_equip
    base_year = runtime.cfg.base_year
    acf = runtime.cfg.appliances.annual_capacity_factor


    """
    ##############################################################
        Annual Capacity Factor
    ##############################################################
    """

    ## NRCan existing stock
    max_note = "Arbitrary annual capacity factor to ensure that existing capacity is sufficient to meet existing peak demand."
    min_note = "95% of ACF upper bound for slack. " + max_note

    # Get existing capacities from NRCan stock and distribute over past vintages
    for tech, row in exs_techs.iterrows():
        if 'appliances' not in row['end_use']: continue
        if row['end_use'] == 'appliances other': continue # no capacity so does not apply

        out_comm = end_use_demands.loc[row['end_use'], 'comm']

        for vint in runtime.tech_vints[tech]:
            if vint + runtime.lifetimes[row['aeo_class']] <= runtime.cfg.future_periods[0]: continue

            # Lower limit
            sql, params = schema_models.LimitAnnualCapacityFactor(
                region=region,
                tech_or_group=tech,
                vintage=vint,
                output_comm=out_comm,
                operator='ge',
                factor=acf * 0.95,
                notes=min_note,
                data_id=utils.data_id(runtime, region),
            ).to_insert_or_ignore_sql()
            curs.execute(sql, params)
            # Upper limit
            sql, params = schema_models.LimitAnnualCapacityFactor(
                region=region,
                tech_or_group=tech,
                vintage=vint,
                output_comm=out_comm,
                operator='le',
                factor=acf,
                notes=max_note,
                data_id=utils.data_id(runtime, region),
            ).to_insert_or_ignore_sql()
            curs.execute(sql, params)



    """
    ##############################################################
        Existing Capacity
    ##############################################################
    """

    note = (
        f"{base_year} stock (NRCan, {base_year}) carried forward to last existing vintage"
         " and distributed evenly over feasible existing vintages."
    )
    ref = runtime.refs.get('nrcan')

    # Table 31: Appliance Stock by Appliance Type and Energy Source
    t31_elc_stk = nrcan.get_compr_db(
        region, 31,
        regions_df=runtime.regions, nrcan_url=runtime.cfg.nrcan_url,
        base_year=base_year, cache_dir=runtime.cfg.cache_dir,
        force_download=runtime.cfg.force_download, first_row=20, last_row=26,
    ) / 1000 # kunit to Munit
    t31_ng_stk = nrcan.get_compr_db(
        region, 31,
        regions_df=runtime.regions, nrcan_url=runtime.cfg.nrcan_url,
        base_year=base_year, cache_dir=runtime.cfg.cache_dir,
        force_download=runtime.cfg.force_download, first_row=38, last_row=39,
    ) / 1000 # kunit to Munit
    pop = runtime.populations[region]

    dems = dict() # sums up demand by end use
    for tech, row in exs_techs.iterrows():
        if 'appliances' not in row['end_use']: continue

        if row['fuels'] == 'electricity':
            existing_cap = t31_elc_stk.loc[row['nrcan_stocks'], base_year]
        elif row['fuels'] == 'natural gas':
            existing_cap = t31_ng_stk.loc[row['nrcan_stocks'], base_year]

        # Add to demand for this end use
        if row['end_use'] not in dems.keys(): dems[row['end_use']] = 0
        dems[row['end_use']] += existing_cap * end_use_demands.loc[row['end_use'], 'c2a'] * acf

        if row['end_use'] == 'appliances other': continue # appliances other has no capacity

        if existing_cap == 0:
            print(f"No existing capacity for appliance {tech} in region {region}. Skipped.")
            continue

        # Distribute existing capacities evenly over feasible vintages
        vints, weights = utils.stock_vintages(
            runtime.lifetimes[row['aeo_class']],
            runtime.cfg.period_step,
            runtime.cfg.future_periods[0],
        )

        # Write existing capacities to database
        for v, vint in enumerate(vints):

            weight = weights[v]

            if vint + runtime.lifetimes[row['aeo_class']] <= runtime.cfg.future_periods[0]: continue

            exs_cap = existing_cap * weight

            sql, params = schema_models.ExistingCapacity(
                region=region,
                tech=tech,
                vintage=vint,
                capacity=exs_cap,
                units=f"({end_use_demands.loc[row['end_use'], 'cap_unit']})",
                notes=note,
                data_source=ref.id,
                dq_cred=1,
                dq_geog=1,
                dq_struc=1,
                dq_tech=1,
                dq_time=3,
                data_id=utils.data_id(runtime, region),
            ).to_insert_or_ignore_sql()
            curs.execute(sql, params)



    """
    ##############################################################
        Demand
    ##############################################################
    """

    ref = runtime.refs.get('nrcan_statcan')

    for end_use, exs_dem in dems.items():
        for period in runtime.cfg.future_periods:

            yr = utils.data_year(period, runtime)
            dem = exs_dem * pop.loc[yr].iloc[0] / pop.loc[base_year].iloc[0]
            note = (
                f"Existing capacity multiplied by an arbitrary {acf} annual capacity factor to ensure existing capacity is "
                f"sufficient to meet peak demand. Indexed to population projection (Statcan) at {yr}."
            )

            sql, params = schema_models.Demand(
                region=region,
                period=period,
                commodity=end_use_demands.loc[end_use, 'comm'],
                demand=dem,
                units=f"({end_use_demands.loc[end_use, 'dem_unit']})",
                notes=note,
                data_source=ref.id,
                data_id=utils.data_id(runtime, region),
            ).to_insert_or_ignore_sql()
            curs.execute(sql, params)



    """
    ##############################################################
        Existing efficiency
    ##############################################################
    """

    # Table 13: Appliance Secondary Energy Use and GHG Emissions by Appliance Type
    t13_sec = nrcan.get_compr_db(
        region, 13,
        regions_df=runtime.regions, nrcan_url=runtime.cfg.nrcan_url,
        base_year=base_year, cache_dir=runtime.cfg.cache_dir,
        force_download=runtime.cfg.force_download, first_row=2, last_row=9,
    ) # PJ

    ref = runtime.refs.get('nrcan')

    ## Efficiency of electricity-only techs from NRCan
    for tech, row in exs_techs.iterrows():
        if 'appliances' not in row['end_use']: continue
        if row['end_use'] in ['appliances clothes dryers', 'appliances cooking ranges']: continue

        eud = end_use_demands.loc[row['end_use']]
        vints = [runtime.cfg.future_periods[0]] if row['end_use'] == 'appliances other' else runtime.tech_vints[tech]

        note = (f"({eud['dem_unit']}/PJ) {base_year} demand divided by {base_year} secondary energy consumption (NRCan, {base_year}). ")

        stock = t31_elc_stk.loc[row['nrcan_stocks'], base_year]
        sec = t13_sec.loc[row['nrcan_stocks'], base_year]

        # Times acf because assumed actual activity is stock times acf
        eff_exs = stock * acf / sec

        ## Existing Efficiency
        for vint in vints:
            if row['end_use'] != 'appliances other': # appliances other has no lifetime
                if vint + runtime.lifetimes[row['aeo_class']] <= runtime.cfg.future_periods[0]: continue

            sql, params = schema_models.Efficiency(
                region=region,
                input_comm=fuel_commodities.loc['electricity', 'comm'],
                tech=tech,
                vintage=vint,
                output_comm=end_use_demands.loc[row['end_use'], 'comm'],
                efficiency=eff_exs,
                notes=note,
                data_source=ref.id,
                dq_cred=1,
                dq_geog=1,
                dq_struc=1,
                dq_tech=1,
                dq_time=3,
                data_id=utils.data_id(runtime, region),
            ).to_insert_or_ignore_sql()
            curs.execute(sql, params)


    ## Cooking ranges and clothes dryers
    # A pain to deal with because both natural gas and electricity variants
    ref = runtime.refs.add('energy_handbook', runtime.cfg.handbook_reference)

    uec_base_year = 2021 # TODO year should be 2022 but download link is broken

    # Generic unit energy consumption of nrcan technologies
    hb_uec = nrcan.get_data(
        f"https://oee.nrcan.gc.ca/corporate/statistics/neud/dpa/data_e/downloads/handbook/Excel/{uec_base_year}/res_00_16_e.xls",
        cache_dir=runtime.cfg.cache_dir, force_download=runtime.cfg.force_download,
        skiprows=7,
    )
    hb_uec: pd.DataFrame = hb_uec.drop('Unnamed: 0', axis=1).set_index('Unnamed: 1').dropna().astype(float, errors='ignore')
    hb_uec *= runtime.cfg.conversion_factors.activity.kwh * 1E6 # /unity to /Munity
    hb_uec = hb_uec.drop(hb_uec.columns[-1], axis='columns') # totals column
    utils.clean_index(hb_uec)
    hb_uec.columns = [int(col) for col in hb_uec.columns]
    hb_uec_elc = hb_uec.iloc[8:14]
    hb_uec_ng = hb_uec.iloc[14:16]

    fuels = ['electricity', 'natural gas'] # fuels to deal with
    hb_uecs = [hb_uec_elc, hb_uec_ng] # reciprocal of base efficiency is "energy consumption"
    
    # Calculate efficiencies for each fuel in Munity/PJ
    for end_use in ['appliances clothes dryers', 'appliances cooking ranges']:

        eud = end_use_demands.loc[end_use]

        # Both ng and elc technologies for this end use
        techs = [exs_techs.loc[(exs_techs['end_use']==end_use) & (exs_techs['fuels']==fuel)].index.values[0] for fuel in fuels]

        for f in [0,1]:

            row = exs_techs.loc[techs[f]] # configuration data
            vints = runtime.tech_vints[techs[f]]

            fuel = fuel_commodities.loc[fuels[f]]
            note = (f"({end_use_demands.loc[row['end_use'], 'dem_unit']}/{fuel['unit']}) From generic unit energy consumption (UEC) of existing stock"
                    " from Energy Use Data Handbook as provincial data cannot be disaggregated by both end use and fuel.")

            # Efficiency in Munity/PJ times acf because assumed actual activity is stock times acf
            eff_exs = 1/hb_uecs[f].loc[row['nrcan_stocks'], uec_base_year] * acf

            ## Existing Efficiency
            for vint in vints:
                if vint + runtime.lifetimes[exs_techs.loc[techs[f],'aeo_class']] <= runtime.cfg.future_periods[0]: continue

                sql, params = schema_models.Efficiency(
                    region=region,
                    input_comm=fuel['comm'],
                    tech=techs[f],
                    vintage=vint,
                    output_comm=end_use_demands.loc[row['end_use'], 'comm'],
                    efficiency=eff_exs,
                    notes=note,
                    data_source=ref.id,
                    dq_cred=1,
                    dq_geog=3,
                    dq_struc=1,
                    dq_tech=3,
                    dq_time=3,
                    data_id=utils.data_id(runtime, region),
                ).to_insert_or_ignore_sql()
                curs.execute(sql, params)



    """
    ##############################################################
        New stock efficiency
    ##############################################################
    """

    ref = runtime.refs.add('nrcan_aeo', f"{runtime.cfg.nrcan_reference}; {runtime.cfg.aeo_reference}")

    # AEO data relevant to this region
    df0 = aeo_res_equip.loc[(aeo_res_equip['Census Division'] == runtime.regions.loc[region, 'us_census_div']) | (aeo_res_equip['Census Division'] == 11)]

    for tech, row in new_techs.iterrows():
        if 'appliances' not in row['end_uses']: continue
        if not row['include_new']: continue

        eud = end_use_demands.loc[row['end_uses']]

        # Relevant to this tech
        df1 = df0.loc[row['aeo_equip']]

        # Get baseline efficiency from existing stock
        nrcan_tech = exs_techs.loc[exs_techs['end_use'] + " - " + exs_techs['description'] == row['nrcan_equiv']].index.values[0]
        base_eff = aeo_res_class.loc[row['aeo_class'], 'Base Efficiency']
        eff_exs = curs.execute(f"SELECT efficiency FROM {schema_models.Efficiency.__table_name__} WHERE region == '{region}' and tech == '{nrcan_tech}'").fetchone()[0]

        vints = runtime.tech_vints[tech]
        for vint in vints:

            yr = utils.data_year(vint, runtime) # end-of-period data year for this vintage

            # Relevant to this vintage
            if type(df1) is pd.DataFrame: new_eff = df1.loc[(df1['First Year']<=yr) & (yr<=df1['Last Year']), 'Efficiency'].iloc[0]
            elif type(df1) is pd.Series: new_eff = df1['Efficiency'] # only one row remaining

            if new_eff >= base_eff: eff = eff_exs * new_eff / base_eff
            else: eff = eff_exs * base_eff / new_eff # efficiency units are energy consumption so invert

            note = (
                f"({eud['dem_unit']}/PJ) Efficiency assumed same as {nrcan_tech} "
                f"but indexed to relative AEO efficiency in {yr} versus baseline efficiency."
            )

            # Write to table — use REPLACE to override the AEO default written by pre_process
            sql, params = schema_models.Efficiency(
                region=region,
                input_comm=fuel_commodities.loc[row['fuel'], 'comm'],
                tech=tech,
                vintage=vint,
                output_comm=eud['comm'],
                efficiency=eff,
                notes=note,
                data_source=ref.id,
                dq_cred=1,
                dq_geog=3,
                dq_struc=1,
                dq_tech=3,
                dq_time=3,
                data_id=utils.data_id(runtime, region),
            ).to_replace_sql()
            curs.execute(sql, params)
