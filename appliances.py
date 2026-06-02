"""
Aggregates data for appliances
Written by Ian David Elder for the CANOE model
"""

import utils
import pandas as pd
import os
import sqlite3
from setup import config

# Shortens lines a bit
base_year = config.params['base_year']
acf = config.params['appliances']['annual_capacity_factor']
end_use_demands = config.end_use_demands
fuel_commodities = config.fuel_commodities
exs_techs = config.existing_techs
new_techs = config.new_techs
aeo_res_class = config.aeo_res_class
aeo_res_equip = config.aeo_res_equip


def aggregate():

    for region in config.model_regions: aggregate_region(region)

    print(f"Appliances data aggregated into {os.path.basename(config.database_file)}\n")



def aggregate_region(region):

    # Connect to the new database file
    conn = sqlite3.connect(config.database_file)
    curs = conn.cursor() # Cursor object interacts with the sqlite db


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

        for vint in config.tech_vints[tech]:
            if vint + config.lifetimes[row['aeo_class']] <= config.model_periods[0]: continue

            # Lower limit
            curs.execute(
                f"""REPLACE INTO
                LimitAnnualCapacityFactor(region, tech, vintage, output_comm, operator, factor, notes, data_id)
                VALUES('{region}', '{tech}', {vint}, '{out_comm}', 'ge', {acf*0.95}, '{min_note}', '{utils.data_id(region)}')"""
            )
            # Upper limit
            curs.execute(
                f"""REPLACE INTO
                LimitAnnualCapacityFactor(region, tech, vintage, output_comm, operator, factor, notes, data_id)
                VALUES('{region}', '{tech}', {vint}, '{out_comm}', 'le', {acf}, '{max_note}', '{utils.data_id(region)}')"""
            )



    """
    ##############################################################
        Existing Capacity
    ##############################################################
    """

    note = (
        f"{base_year} stock (NRCan, {base_year}) carried forward to last existing vintage"
         " and distributed evenly over feasible existing vintages."
    )
    ref = config.refs.get('nrcan')

    # Table 31: Appliance Stock by Appliance Type and Energy Source
    t31_elc_stk = utils.get_compr_db(region, 31, 20, 26) # kunit
    t31_ng_stk = utils.get_compr_db(region, 31, 38, 39) # kunit
    pop = config.populations[region]

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
        vints, weights = utils.stock_vintages(config.lifetimes[row['aeo_class']])

        # Write existing capacities to database
        for v, vint in enumerate(vints):

            weight = weights[v]

            if vint + config.lifetimes[row['aeo_class']] <= config.model_periods[0]: continue

            exs_cap = existing_cap * weight

            curs.execute(
                f"""REPLACE INTO
                ExistingCapacity(region, tech, vintage, capacity, units,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{tech}', {vint}, {exs_cap}, '({config.end_use_demands.loc[row['end_use'], 'cap_unit']})',
                '{note}', '{ref.id}', 1, 1, 1, 1, 3, '{utils.data_id(region)}')"""
            )
        


    """
    ##############################################################
        Demand
    ##############################################################
    """

    ref = config.refs.get('nrcan_statcan')

    for end_use, exs_dem in dems.items():
        for period in config.model_periods:

            yr = utils.data_year(period)
            dem = exs_dem * pop.loc[yr].iloc[0] / pop.loc[base_year].iloc[0]
            note = (
                f"Existing capacity multiplied by an arbitrary {acf} annual capacity factor to ensure existing capacity is "
                f"sufficient to meet peak demand. Indexed to population projection (Statcan) at {yr}."
            )

            curs.execute(
                f"""REPLACE INTO
                Demand(region, period, commodity, demand, units,
                notes, data_source, data_id)
                VALUES('{region}', {period}, '{config.end_use_demands.loc[end_use, 'comm']}', {dem}, '({config.end_use_demands.loc[end_use, 'dem_unit']})',
                '{note}', '{ref.id}', '{utils.data_id(region)}')"""
            )
        


    """
    ##############################################################
        Existing efficiency
    ##############################################################
    """

    # Table 13: Appliance Secondary Energy Use and GHG Emissions by Appliance Type
    t13_sec = utils.get_compr_db(region, 13, 2, 9) # PJ
    
    ref = config.refs.get('nrcan')

    ## Efficiency of electricity-only techs from NRCan
    for tech, row in exs_techs.iterrows():
        if 'appliances' not in row['end_use']: continue
        if row['end_use'] in ['appliances clothes dryers', 'appliances cooking ranges']: continue

        vints = [config.model_periods[0]] if row['end_use'] == 'appliances other' else config.tech_vints[tech]

        note = (f"(kunity/PJ) {base_year} demand divided by {base_year} secondary energy consumption (NRCan, {base_year}). ")

        stock = t31_elc_stk.loc[row['nrcan_stocks'], base_year] # kunit
        sec = t13_sec.loc[row['nrcan_stocks'], base_year]

        # Times acf because assumed actual activity is stock times acf
        eff_exs = stock * acf / sec

        ## Existing Efficiency
        for vint in vints:
            if row['end_use'] != 'appliances other': # appliances other has no lifetime
                if vint + config.lifetimes[row['aeo_class']] <= config.model_periods[0]: continue

            curs.execute(
                f"""REPLACE INTO
                Efficiency(region, input_comm, tech, vintage, output_comm, efficiency, 
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{fuel_commodities.loc['electricity', 'comm']}', '{tech}', {vint}, '{end_use_demands.loc[row['end_use'], 'comm']}', {eff_exs},
                '{note}', '{ref.id}', 1, 1, 1, 1, 3, '{utils.data_id(region)}')"""
            )


    ## Cooking ranges and clothes dryers
    # A pain to deal with because both natural gas and electricity variants
    ref = config.refs.add('energy_handbook', config.params['handbook_reference'])

    uec_base_year = 2021 # TODO year should be 2022 but download link is broken

    # Generic unit energy consumption of nrcan technologies
    hb_uec = utils.get_data(f"https://oee.nrcan.gc.ca/corporate/statistics/neud/dpa/data_e/downloads/handbook/Excel/{uec_base_year}/res_00_16_e.xls", skiprows=7)
    hb_uec: pd.DataFrame = hb_uec.drop('Unnamed: 0', axis=1).set_index('Unnamed: 1').dropna().astype(float, errors='ignore')
    hb_uec *= config.params['conversion_factors']['activity']['kwh'] * 1000 # /unity to /kunity
    hb_uec = hb_uec.drop(hb_uec.columns[-1], axis='columns') # totals column
    utils.clean_index(hb_uec)
    hb_uec.columns = [int(col) for col in hb_uec.columns]
    hb_uec_elc = hb_uec.iloc[8:14]
    hb_uec_ng = hb_uec.iloc[14:16]

    fuels = ['electricity', 'natural gas'] # fuels to deal with
    hb_uecs = [hb_uec_elc, hb_uec_ng] # reciprocal of base efficiency is "energy consumption"
    
    # Calculate efficiencies for each fuel in kunity/PJ
    for end_use in ['appliances clothes dryers', 'appliances cooking ranges']:

        # Both ng and elc technologies for this end use
        techs = [exs_techs.loc[(exs_techs['end_use']==end_use) & (exs_techs['fuels']==fuel)].index.values[0] for fuel in fuels]

        for f in [0,1]:

            row = exs_techs.loc[techs[f]] # configuration data
            vints = config.tech_vints[techs[f]]

            fuel = fuel_commodities.loc[fuels[f]]
            note = (f"({config.end_use_demands.loc[row['end_use'], 'dem_unit']}/{fuel['unit']}) From generic unit energy consumpion (UEC) of existing stock"
                    " from Energy Use Data Handbook as provincial data cannot be disaggregated by both end use and fuel.")

            # Efficiency in kunity/PJ times acf because assumed actual activity is stock times acf
            eff_exs = 1/hb_uecs[f].loc[row['nrcan_stocks'], uec_base_year] * acf

            ## Existing Efficiency
            for vint in vints:
                if vint + config.lifetimes[exs_techs.loc[techs[f],'aeo_class']] <= config.model_periods[0]: continue
                
                curs.execute(
                    f"""REPLACE INTO
                    Efficiency(region, input_comm, tech, vintage, output_comm, efficiency,
                    notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                    VALUES('{region}', '{fuel['comm']}', '{techs[f]}', {vint}, '{config.end_use_demands.loc[row['end_use'], 'comm']}', {eff_exs},
                    '{note}', '{ref.id}', 1, 3, 1, 3, 3, '{utils.data_id(region)}')"""
                )
            


    """
    ##############################################################
        New stock efficiency
    ##############################################################
    """

    ref = config.refs.add('nrcan_aeo', f"{config.params['nrcan_reference']}; {config.params['aeo_reference']}")

    # AEO data relevant to this region
    df0 = aeo_res_equip.loc[(aeo_res_equip['Census Division'] == config.regions.loc[region, 'us_census_div']) | (aeo_res_equip['Census Division'] == 11)]

    for tech, row in new_techs.iterrows():
        if 'appliances' not in row['end_uses']: continue
        if not row['include_new']: continue

        # Relevant to this tech
        df1 = df0.loc[row['aeo_equip']]
        
        # Get baseline efficiency from existing stock
        nrcan_tech = exs_techs.loc[exs_techs['end_use'] + " - " + exs_techs['description'] == row['nrcan_equiv']].index.values[0]
        base_eff = aeo_res_class.loc[row['aeo_class'], 'Base Efficiency']
        eff_exs = curs.execute(f"SELECT efficiency FROM Efficiency WHERE region == '{region}' and tech == '{nrcan_tech}'").fetchone()[0]

        vints = config.tech_vints[tech]
        for vint in vints:

            yr = utils.data_year(vint) # end-of-period data year for this vintage

            # Relevant to this vintage
            if type(df1) is pd.DataFrame: new_eff = df1.loc[(df1['First Year']<=yr) & (yr<=df1['Last Year']), 'Efficiency'].iloc[0]
            elif type(df1) is pd.Series: new_eff = df1['Efficiency'] # only one row remaining

            if new_eff >= base_eff: eff = eff_exs * new_eff / base_eff
            else: eff = eff_exs * base_eff / new_eff # efficiency units are energy consumption so invert

            note = (
                f"(kunity/PJ) Efficiency assumed same as {nrcan_tech} "
                f"but indexed to relative AEO efficiency in {yr} versus baseline efficiency."
            )

            # Write to table
            curs.execute(
                f"""REPLACE INTO
                Efficiency(region, input_comm, tech, vintage, output_comm, efficiency,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{fuel_commodities.loc[row['fuel'], 'comm']}', '{tech}', {vint}, '{end_use_demands.loc[row['end_uses'], 'comm']}', {eff},
                '{note}', '{ref.id}', 1, 3, 1, 3, 3, '{utils.data_id(region)}')"""
            )

    conn.commit()
    conn.close()



if __name__ == "__main__":
    
    aggregate()