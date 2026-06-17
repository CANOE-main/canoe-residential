"""
Aggregates data for residential lighting
Written by Ian David Elder for the CANOE model
"""

import canoe_residential.utils as utils
import canoe_residential.nrcan as nrcan
import canoe_residential.statcan as statcan
import pandas as pd
import os
import numpy as np
import sqlite3
from canoe_schema.v4_0 import models as schema_models
from canoe_residential.currency_conversion import conv_curr
from canoe_residential.common import ResidentialRuntime


# Gets a value from aeo lighting data
def get_aeo_value(code, metric, vintage, aeo_data):

    # Get data from latest preceding vintage
    vints = np.array([int(col) for col in aeo_data.columns if col.isdecimal()])
    if vintage < min(vints): last_vint = vints[0]
    else: last_vint = vints[vints < vintage][-1]
    value = aeo_data.loc[aeo_data['metric']==metric].loc[code, str(last_vint)]

    # If no value for that vintage, take existing stock value
    if pd.isna(value): value = aeo_data.loc[aeo_data['metric']==metric].loc[code, 'existing']

    return value



# Gets relative usage rates of bulb types for a province from Statcan table 38100048
def get_usage(region, runtime: ResidentialRuntime, lgt_usage):

    # Just filtering and pivoting the table to show bulb types as rows and years as columns
    usage = lgt_usage.loc[(lgt_usage['GEO'] == runtime.regions.loc[region, 'description'])][['Type of energy-saving light','REF_DATE','VALUE']].set_index('Type of energy-saving light')
    usage = usage.pivot_table(values='VALUE', index=usage.index, columns='REF_DATE', aggfunc='first')

    # The residential end use survey was 2018 so interpolate between 2017/2019
    return (usage[2017] + usage[2019])/2



def aggregate(runtime: ResidentialRuntime, conn: sqlite3.Connection):

    for region in runtime.cfg.province_list: aggregate_region(region, runtime, conn)

    print(f"Lighting data aggregated into {os.path.basename(runtime.cfg.db_dir)}\n")



def aggregate_region(region: str, runtime: ResidentialRuntime, conn: sqlite3.Connection):

    curs = conn.cursor() # Cursor object interacts with the sqlite db

    base_year = runtime.cfg.base_year
    conv = runtime.cfg.conversion_factors.lighting
    in_comm = runtime.fuel_commodities.loc['electricity']
    lighting = runtime.end_use_demands.loc['lighting']
    acf = runtime.cfg.lighting.annual_capacity_factor

    runtime.refs.add('ontario_lighting_stock', runtime.cfg.lighting.on_stock_ref)
    runtime.refs.add('lighting_usage', runtime.cfg.lighting.usage_ref)

    # Get provincial data on relative usage of different bulb types from Statcan table 38100048
    lgt_usage = statcan.get_statcan_table(38100048, runtime.cfg.cache_dir, runtime.cfg.force_download)
    lgt_usage['GEO'] = lgt_usage['GEO'].str.lower()

    # Configuration file for lighting technologies, including Ontario shares data from residential end use survey
    exs_techs = pd.read_csv(runtime.cfg.input_files_dir + '/existing_lighting_technologies.csv', index_col=0)
    aeo_data = pd.read_csv(runtime.cfg.input_files_dir + '/aeo_lighting_data.csv', index_col=0)
    aeo_techs = pd.read_csv(runtime.cfg.input_files_dir + '/new_lighting_technologies.csv', index_col=0)

    # Ontario usage as a baseline
    on_usage = get_usage('ON', runtime, lgt_usage)


    """
    ##############################################################
        Annual Capacity Factor
    ##############################################################
    """

    # ACF mostly arbitrary but affects lifetime
    acf_note = runtime.cfg.lighting.acf_note
    min_note = acf_note + " 95% of upper bound for slack."
    ref = runtime.refs.add('lighting_acf', runtime.cfg.lighting.acf_reference)

    for code, row in aeo_techs.iterrows():

        if not row['include_new']: continue

        for vintage in runtime.cfg.future_periods:

            sql, params = schema_models.LimitAnnualCapacityFactor(
                region=region,
                tech_or_group=row['tech'],
                vintage=vintage,
                output_comm=lighting['comm'],
                operator='ge',
                factor=acf * 0.95,
                notes=min_note,
                data_source=ref.id,
                dq_cred=1,
                dq_geog=3,
                dq_struc=3,
                dq_tech=1,
                dq_time=4,
                data_id=utils.data_id(runtime, region),
            ).to_insert_or_ignore_sql()
            curs.execute(sql, params)
            sql, params = schema_models.LimitAnnualCapacityFactor(
                region=region,
                tech_or_group=row['tech'],
                vintage=vintage,
                output_comm=lighting['comm'],
                operator='le',
                factor=acf,
                notes=acf_note,
                data_source=ref.id,
                dq_cred=1,
                dq_geog=3,
                dq_struc=3,
                dq_tech=1,
                dq_time=4,
                data_id=utils.data_id(runtime, region),
            ).to_insert_or_ignore_sql()
            curs.execute(sql, params)



    """
    ##############################################################
        Demand
    ##############################################################
    """

    # TODO The major challenge of lighting is estimating existing capacity of lighting types
    # If we had better data for this everything would be fine... but we only have for Ontario
    # So we take stock data for Ontario and index it to usage rates from a Statcan survey per province

    ref = runtime.refs.get('nrcan_statcan')

    # Get usage of bulb types for this region relative to Ontario
    # Because we have actual shares data for Ontario (residential end use survey)
    reg_usage = get_usage(region, runtime, lgt_usage)
    usage_index = reg_usage / on_usage

    # Calculate regional shares by indexing ontario shares to Statcan usage survey
    reg_shares = exs_techs.rename({'on_share_sf':'share_sf', 'on_share_mf':'share_mf'}, axis=1)
    for code in reg_shares.index.values:
        statcan_cat = exs_techs.loc[code, 'statcan_category']
        reg_shares.loc[code, ['share_sf', 'share_mf']] *= usage_index.loc[statcan_cat]
    for col in reg_shares[['share_sf', 'share_mf']].columns: reg_shares[col] /= reg_shares[col].sum() # reset to sum 100%

    # Table 14: Total Households by Building Type and Energy Source
    t14 = nrcan.get_compr_db(
        region, 14,
        regions_df=runtime.regions, nrcan_url=runtime.cfg.nrcan_url,
        base_year=base_year, cache_dir=runtime.cfg.cache_dir,
        force_download=runtime.cfg.force_download, first_row=9, last_row=12,
    )[base_year] / 100 # % shares

    # Aggregate subcategories of housing into single-family and multi-family
    for cat, subcats in runtime.cfg.housing_categories.items():
        subcats = subcats.split('+')
        t14[cat] = sum([t14[subcat] for subcat in subcats])
        t14 = t14.drop(subcats)

    # Mapping existing stock AEO data to existing technologies
    for code, _exs in exs_techs.iterrows():
        data = aeo_data.loc[code].pivot_table(values='existing', index='code', columns='metric')
        for metric in data.columns: exs_techs.loc[code, metric] = data[metric].iloc[0]

    # Unit conversion
    exs_techs['efficacy'] *= conv.efficacy # efficacy lm/W to Glmy/PJ
    exs_techs['cost_maintain'] *= conv.cost # $/klmy to $/Glmy
    exs_techs['cost_maintain'] = conv_curr(runtime, exs_techs['cost_maintain'])
    exs_techs['lamp_life'] = round(exs_techs['lamp_life'] * conv.lifetime / acf)

    # Finally, calculate the average efficacy of existing lighting stock, indexed to shares of single-family vs multi-family housing
    exs_eff = 0 # Glmy/PJ
    for code_exs, row_exs in reg_shares.iterrows():
        reg_shares.loc[code_exs, 'share_tot'] = np.dot(row_exs[['share_sf','share_mf']].values, t14.values)
        exs_eff += exs_techs.loc[code_exs, 'efficacy'] * reg_shares.loc[code_exs, 'share_tot']

    # Table 3: Lighting Secondary Energy Use and GHG Emissions
    sec = nrcan.get_compr_db(
        region, 3,
        regions_df=runtime.regions, nrcan_url=runtime.cfg.nrcan_url,
        base_year=base_year, cache_dir=runtime.cfg.cache_dir,
        force_download=runtime.cfg.force_download, first_row=1, last_row=1,
    )[base_year].iloc[0]

    # Demand is secondary energy times 2018 average lighting stock efficacy, indexed to population growth
    pop = runtime.populations[region]
    dem = exs_eff * sec * pop / pop.loc[base_year]

    # Write demand to database
    for period in runtime.cfg.future_periods:
        yr = utils.data_year(period, runtime)
        note = (
            f"{base_year} secondary energy (NRCan, {base_year}) multiplied by average efficacy "
            "(efficiency) of existing lighting stock. "
            f"Indexed to population projection (Statcan) at {yr}"
        )
        sql, params = schema_models.Demand(
            region=region,
            period=period,
            commodity=lighting['comm'],
            demand=dem.loc[yr].iloc[0],
            units=f"({lighting['dem_unit']})",
            notes=note,
            data_source=ref.id,
            dq_cred=1,
            dq_geog=3,
            dq_struc=4,
            dq_tech=2,
            dq_time=4,
            data_id=utils.data_id(runtime, region),
        ).to_insert_or_ignore_sql()
        curs.execute(sql, params)



    """
    ##############################################################
        Existing stock data
    ##############################################################
    """

    # Existing capacity in Glmy at time of first model period when indexed to population growth
    exs_techs['existing_capacity'] = reg_shares['share_tot'] * dem.loc[runtime.cfg.future_periods[0]].iloc[0] / acf

    # Distribute existing capacities over feasible past vintages
    for code, exs in exs_techs.iterrows():

        lifetime = exs['lamp_life']
        vints, weights = utils.stock_vintages(lifetime, runtime.cfg.period_step, runtime.cfg.future_periods[0])
        if max(vints) + lifetime <= runtime.cfg.future_periods[0]: continue # this technology never reaches the first model period

        existing_cap = exs['existing_capacity']

        if existing_cap == 0:
            print(f"No existing capacity for lighting tech {exs['tech']} in region {region}. Skipped.")
            continue

        aeo_note = f"Assumed same as {aeo_techs.loc[code, 'tech']}."

        tech_desc = f"lighting - {exs.loc['description']}"
        sql, params = schema_models.Technology(
            tech=exs['tech'],
            flag='p',
            sector='residential',
            annual=1,
            description=tech_desc,
            data_id=utils.data_id(runtime),
        ).to_insert_or_ignore_sql()
        curs.execute(sql, params)
        unit = f"{lighting['dem_unit']}/{lighting['cap_unit']}.y" # ACT/CAP.y
        sql, params = schema_models.CapacityToActivity(
            region=region,
            tech=exs['tech'],
            c2a=1,
            notes=f"({unit})",
            data_id=utils.data_id(runtime, region),
        ).to_insert_or_ignore_sql()
        curs.execute(sql, params)
        sql, params = schema_models.LifetimeTech(
            region=region,
            tech=exs['tech'],
            lifetime=lifetime,
            notes=f"(y) {aeo_note}",
            data_source=ref.id,
            dq_cred=1,
            dq_geog=3,
            dq_struc=2,
            dq_tech=2,
            dq_time=3,
            data_id=utils.data_id(runtime, region),
        ).to_insert_or_ignore_sql()
        curs.execute(sql, params)

        # Some lighting techs didn't come around that long ago so restrict the oldest vintage
        if not pd.isna(exs['oldest_vint']): vints = [vint for vint in vints if vint >= exs['oldest_vint']]

        # Write existing data to database
        for v in range(len(vints)):

            vint = vints[v]
            weight = weights[v]

            if vint + lifetime <= runtime.cfg.future_periods[0]: continue

            exs_cap = existing_cap * weight

            note = (f"Ontario existing stock of residential bulb types by housing type (IESO, 2018) "
                    f"multiplied by housing stock by type (NRCan, {base_year}). "
                    f"Indexed to relative usage of bulb types by province versus Ontario (Statcan, 2018) "
                    f"and to projected population (Statcan) in {runtime.cfg.future_periods[0]}")
            ref = runtime.refs.add('lighting_existing_capacity',
                f"{runtime.cfg.lighting.on_stock_ref}; "
                f"{runtime.cfg.nrcan_reference}; "
                f"{runtime.cfg.lighting.usage_ref}; "
                f"{runtime.cfg.aeo_reference}"
            )
            sql, params = schema_models.ExistingCapacity(
                region=region,
                tech=exs['tech'],
                vintage=vint,
                capacity=exs_cap,
                units=f"({lighting['cap_unit']})",
                notes=note,
                data_source=ref.id,
                dq_cred=1,
                dq_geog=2,
                dq_struc=3,
                dq_tech=3,
                dq_time=4,
                data_id=utils.data_id(runtime, region),
            ).to_insert_or_ignore_sql()
            curs.execute(sql, params)

            ref = runtime.refs.get('aeo')
            sql, params = schema_models.Efficiency(
                region=region,
                input_comm=in_comm['comm'],
                tech=exs['tech'],
                vintage=vint,
                output_comm=lighting['comm'],
                efficiency=exs['efficacy'],
                notes=f"({lighting['dem_unit']}/{in_comm['unit']}) {aeo_note}",
                data_source=ref.id,
                dq_cred=1,
                dq_geog=2,
                dq_struc=3,
                dq_tech=3,
                dq_time=4,
                data_id=utils.data_id(runtime, region),
            ).to_insert_or_ignore_sql()
            curs.execute(sql, params)

            sql, params = schema_models.LimitAnnualCapacityFactor(
                region=region,
                tech_or_group=exs['tech'],
                vintage=vint,
                output_comm=lighting['comm'],
                operator='ge',
                factor=acf * 0.95,
                notes=min_note,
                data_source=ref.id,
                dq_cred=1,
                dq_geog=3,
                dq_struc=2,
                dq_tech=2,
                dq_time=4,
                data_id=utils.data_id(runtime, region),
            ).to_insert_or_ignore_sql()
            curs.execute(sql, params)
            sql, params = schema_models.LimitAnnualCapacityFactor(
                region=region,
                tech_or_group=exs['tech'],
                vintage=vint,
                output_comm=lighting['comm'],
                operator='le',
                factor=acf,
                notes=acf_note,
                data_source=ref.id,
                dq_cred=1,
                dq_geog=3,
                dq_struc=2,
                dq_tech=2,
                dq_time=4,
                data_id=utils.data_id(runtime, region),
            ).to_insert_or_ignore_sql()
            curs.execute(sql, params)

            for period in runtime.cfg.future_periods:
                if vint > period or vint + lifetime <= period: continue

                sql, params = schema_models.CostFixed(
                    region=region,
                    period=period,
                    tech=exs['tech'],
                    vintage=vint,
                    cost=exs['cost_maintain'],
                    units=f"(M$/{lighting['cap_unit']}.y)",
                    notes=aeo_note,
                    data_source=ref.id,
                    dq_cred=1,
                    dq_geog=2,
                    dq_struc=3,
                    dq_tech=3,
                    dq_time=4,
                    data_id=utils.data_id(runtime, region),
                ).to_insert_or_ignore_sql()
                curs.execute(sql, params)



    """
    ##############################################################
        New stock data
    ##############################################################
    """

    for code, aeo in aeo_techs.iterrows():

        if not aeo['include_new']: continue

        tech_desc = f"lighting - {aeo['description']}"
        sql, params = schema_models.Technology(
            tech=aeo['tech'],
            flag='p',
            sector='residential',
            annual=1,
            description=tech_desc,
            data_id=utils.data_id(runtime),
        ).to_insert_or_ignore_sql()
        curs.execute(sql, params)

        # Vintages for new stock are model periods
        for vint in runtime.cfg.future_periods:

            yr = utils.data_year(vint, runtime) # end-of-period data year for this vintage

            # Lifetime from aeo data converted from hours to years using the annual capacity factor and rounded
            lifetime = round(get_aeo_value(code, 'lamp_life', yr, aeo_data) * conv.lifetime / acf)

            ## LifetimeProcess
            # Using lifetime process because some bulb lives might improve over model periods in aeo data
            note = f"(y) AEO {yr} lamp life in hours divided by annual capacity factor (DOE, 2012)."
            ref = runtime.refs.get('aeo')
            sql, params = schema_models.LifetimeProcess(
                region=region,
                tech=aeo['tech'],
                vintage=vint,
                lifetime=lifetime,
                notes=note,
                data_source=ref.id,
                dq_cred=1,
                dq_geog=3,
                dq_struc=3,
                dq_tech=1,
                dq_time=2,
                data_id=utils.data_id(runtime, region),
            ).to_insert_or_ignore_sql()
            curs.execute(sql, params)

            ## Efficiency
            eff = conv.efficacy * get_aeo_value(code, 'efficacy', yr, aeo_data)
            sql, params = schema_models.Efficiency(
                region=region,
                input_comm=in_comm['comm'],
                tech=aeo['tech'],
                vintage=vint,
                output_comm=lighting['comm'],
                efficiency=eff,
                notes=f"({lighting['dem_unit']}/{in_comm['unit']}) from AEO for {yr}",
                data_source=ref.id,
                dq_cred=1,
                dq_geog=3,
                dq_struc=3,
                dq_tech=1,
                dq_time=2,
                data_id=utils.data_id(runtime, region),
            ).to_insert_or_ignore_sql()
            curs.execute(sql, params)

            ## CostInvest
            cost_invest = conv.cost * get_aeo_value(code, 'cost_install', yr, aeo_data)
            cost_invest = conv_curr(runtime, cost_invest)
            sql, params = schema_models.CostInvest(
                region=region,
                tech=aeo['tech'],
                vintage=vint,
                cost=cost_invest,
                units=f"(M$/{lighting['cap_unit']})",
                notes=f"from AEO for {yr}",
                data_source=ref.id,
                dq_cred=1,
                dq_geog=3,
                dq_struc=3,
                dq_tech=1,
                dq_time=2,
                data_id=utils.data_id(runtime, region),
            ).to_insert_or_ignore_sql()
            curs.execute(sql, params)

            for period in runtime.cfg.future_periods:

                # Can't pay for a technology if it can't exist
                if period < vint or vint + lifetime <= period: continue

                ## CostFixed
                cost_fixed = conv.cost * get_aeo_value(code, 'cost_maintain', yr, aeo_data)
                cost_fixed = conv_curr(runtime, cost_fixed)
                sql, params = schema_models.CostFixed(
                    region=region,
                    period=period,
                    tech=aeo['tech'],
                    vintage=vint,
                    cost=cost_fixed,
                    units=f"(M$/{lighting['cap_unit']}.y)",
                    notes=f"from AEO for {yr}",
                    data_source=ref.id,
                    dq_cred=1,
                    dq_geog=3,
                    dq_struc=3,
                    dq_tech=1,
                    dq_time=2,
                    data_id=utils.data_id(runtime, region),
                ).to_insert_or_ignore_sql()
                curs.execute(sql, params)



if __name__ == "__main__":

    from canoe_residential.setup import build_runtime
    import sqlite3 as _sqlite3
    rt = build_runtime()
    with _sqlite3.connect(rt.cfg.db_dir) as _conn:
        aggregate(rt, _conn)
