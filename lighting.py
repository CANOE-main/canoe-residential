"""
Aggregates data for residential lighting
Written by Ian David Elder for the CANOE model
"""

import utils
import pandas as pd
import os
import numpy as np
import sqlite3
from currency_conversion import conv_curr
from setup import config

# Shortens lines a bit
base_year = config.params['base_year']
config.refs.add('ontario_lighting_stock', config.params['lighting']['on_stock_ref'])
config.refs.add('lighting_usage', config.params['lighting']['usage_ref'])
conv = config.params['conversion_factors']['lighting']

# Some common variables
in_comm = config.fuel_commodities.loc['electricity']
lighting = config.end_use_demands.loc['lighting']
acf = config.params['lighting']['annual_capacity_factor']

"""
##############################################################
    Non-regional data
##############################################################
"""

# Get provincial data on relative usage of different bulb types from Statcan table 38100048
lgt_usage = utils.get_statcan_table(38100048)
lgt_usage['GEO'] = lgt_usage['GEO'].str.lower()

# Configuration file for lighting technologies, including Ontario shares data from residential end use survey
exs_techs = pd.read_csv(config.input_files + '/existing_lighting_technologies.csv', index_col=0)
aeo_data = pd.read_csv(config.input_files + '/aeo_lighting_data.csv', index_col=0)
aeo_techs = pd.read_csv(config.input_files + '/new_lighting_technologies.csv', index_col=0)



# Gets a value from aeo lighting data
def get_aeo_value(code, metric, vintage):
    
    # Get data from latest preceding vintage
    vints = np.array([int(col) for col in aeo_data.columns if col.isdecimal()])
    if vintage < min(vints): last_vint = vints[0]
    else: last_vint = vints[vints < vintage][-1]
    value = aeo_data.loc[aeo_data['metric']==metric].loc[code, str(last_vint)]

    # If no value for that vintage, take existing stock value
    if pd.isna(value): value = aeo_data.loc[aeo_data['metric']==metric].loc[code, 'existing']
    
    return value



# Gets relative usage rates of bulb types for a province from Statcan table 38100048
def get_usage(region):

    # Just filtering and pivoting the table to show bulb types as rows and years as columns
    usage = lgt_usage.loc[(lgt_usage['GEO'] == config.regions.loc[region, 'description'])][['Type of energy-saving light','REF_DATE','VALUE']].set_index('Type of energy-saving light')
    usage = usage.pivot_table(values='VALUE', index=usage.index, columns='REF_DATE', aggfunc='first')

    # The residential end use survey was 2018 so interpolate between 2017/2019
    return (usage[2017] + usage[2019])/2

# Ontario usage as a baseline
on_usage = get_usage('ON')



def aggregate():

    for region in config.model_regions: aggregate_region(region)

    print(f"Lighting data aggregated into {os.path.basename(config.database_file)}\n")



def aggregate_region(region):

    # Connect to the new database file
    conn = sqlite3.connect(config.database_file)
    curs = conn.cursor() # Cursor object interacts with the sqlite db



    """
    ##############################################################
        Annual Capacity Factor
    ##############################################################
    """

    # ACF mostly arbitrary but affects lifetime
    acf_note = config.params['lighting']['acf_note']
    min_note = acf_note + " 95% of upper bound for slack."
    ref = config.refs.add('lighting_acf', config.params['lighting']['acf_reference'])

    for code, row in aeo_techs.iterrows():

        if not row['include_new']: continue
        
        for vintage in config.model_periods:

            curs.execute(
                f"""REPLACE INTO
                LimitAnnualCapacityFactor(region, tech, vintage, output_comm, operator, factor,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{row['tech']}', {vintage}, '{lighting['comm']}', 'ge', {acf*0.95},
                '{min_note}', '{ref.id}', 1, 3, 3, 1, 4, '{utils.data_id(region)}')"""
            )
            curs.execute(
                f"""REPLACE INTO
                LimitAnnualCapacityFactor(region, tech, vintage, output_comm, operator, factor,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{row['tech']}', {vintage}, '{lighting['comm']}', 'le', {acf},
                '{acf_note}', '{ref.id}', 1, 3, 3, 1, 4, '{utils.data_id(region)}')"""
            )



    """
    ##############################################################
        Demand
    ##############################################################
    """

    # TODO The major challenge of lighting is estimating existing capacity of lighting types
    # If we had better data for this everything would be fine... but we only have for Ontario
    # So we take stock data for Ontario and index it to usage rates from a Statcan survey per province

    ref = config.refs.get('nrcan_statcan')
    
    # Get usage of bulb types for this region relative to Ontario
    # Because we have actual shares data for Ontario (residential end use survey)
    reg_usage = get_usage(region)
    usage_index = reg_usage / on_usage

    # Calculate regional shares by indexing ontario shares to Statcan usage survey
    reg_shares = exs_techs.rename({'on_share_sf':'share_sf', 'on_share_mf':'share_mf'}, axis=1)
    for code in reg_shares.index.values:
        statcan_cat = exs_techs.loc[code, 'statcan_category']
        reg_shares.loc[code, ['share_sf', 'share_mf']] *= usage_index.loc[statcan_cat]
    for col in reg_shares[['share_sf', 'share_mf']].columns: reg_shares[col] /= reg_shares[col].sum() # reset to sum 100%

    # Table 14: Total Households by Building Type and Energy Source
    t14 = utils.get_compr_db(region, 14, 9, 12)[base_year] / 100 # % shares
    
    # Aggregate subcategories of housing into single-family and multi-family
    for cat, subcats in config.params['housing_categories'].items():
        subcats = subcats.split('+')
        t14[cat] = sum([t14[subcat] for subcat in subcats])
        t14 = t14.drop(subcats)
    
    # Mapping existing stock AEO data to existing technologies
    for code, _exs in exs_techs.iterrows():
        data = aeo_data.loc[code].pivot_table(values='existing', index='code', columns='metric')
        for metric in data.columns: exs_techs.loc[code, metric] = data[metric].iloc[0]
    
    # Unit conversion
    exs_techs['efficacy'] *= conv['efficacy'] # efficacy lm/W to Glmy/PJ
    exs_techs['cost_maintain'] *= conv['cost'] # $/klmy to $/Glmy
    exs_techs['cost_maintain'] = conv_curr(exs_techs['cost_maintain'])
    exs_techs['lamp_life'] = round(exs_techs['lamp_life'] * conv['lifetime'] / acf)

    # Finally, calculate the average efficacy of existing lighting stock, indexed to shares of single-family vs multi-family housing
    exs_eff = 0 # Glmy/PJ
    for code_exs, row_exs in reg_shares.iterrows():
        reg_shares.loc[code_exs, 'share_tot'] = np.dot(row_exs[['share_sf','share_mf']].values, t14.values)
        exs_eff += exs_techs.loc[code_exs, 'efficacy'] * reg_shares.loc[code_exs, 'share_tot']

    # Table 3: Lighting Secondary Energy Use and GHG Emissions
    sec = utils.get_compr_db(region, 3, 1, 1)[base_year].iloc[0]

    # Demand is secondary energy times 2018 average lighting stock efficacy, indexed to population growth
    pop = config.populations[region]
    dem = exs_eff * sec * pop / pop.loc[base_year]

    # Write demand to database
    for period in config.model_periods:
        yr = utils.data_year(period)
        note = (
            f"{base_year} secondary energy (NRCan, {base_year}) multiplied by average efficacy "
            "(efficiency) of existing lighting stock. "
            f"Indexed to population projection (Statcan) at {yr}"
        )
        curs.execute(
            f"""REPLACE INTO
            Demand(region, period, commodity, demand, units,
            notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
            VALUES('{region}', {period}, '{lighting['comm']}', {dem.loc[yr].iloc[0]}, '({lighting['dem_unit']})',
            '{note}', '{ref.id}', 1, 3, 4, 2, 4, '{utils.data_id(region)}')"""
        )
        


    """
    ##############################################################
        Existing stock data
    ##############################################################
    """

    # Existing capacity in Glmy at time of first model period when indexed to population growth
    exs_techs['existing_capacity'] = reg_shares['share_tot'] * dem.loc[config.model_periods[0]].iloc[0] / acf
    
    # Distribute existing capacities over feasible past vintages
    for code, exs in exs_techs.iterrows():

        lifetime = exs['lamp_life']
        vints, weights = utils.stock_vintages(lifetime)
        if max(vints) + lifetime <= config.model_periods[0]: continue # this technology never reaches the first model period

        existing_cap = exs['existing_capacity']

        if existing_cap == 0:
            print(f"No existing capacity for lighting tech {exs['tech']} in region {region}. Skipped.")
            continue

        aeo_note = f"Assumed same as {aeo_techs.loc[code, 'tech']}."
        
        tech_desc = f"lighting - {exs.loc['description']}"
        curs.execute(
            f"""REPLACE INTO
            Technology(tech, flag, sector, annual, description, data_id)
            VALUES('{exs['tech']}', 'p', 'residential', 1, '{tech_desc}', '{utils.data_id()}')"""
        )
        unit = f"{lighting['dem_unit']}/{lighting['cap_unit']}.y" # ACT/CAP.y
        curs.execute(
            f"""REPLACE INTO
            CapacityToActivity(region, tech, c2a, notes, data_id)
            VALUES('{region}', '{exs['tech']}', 1, '({unit})', '{utils.data_id(region)}')"""
        )
        curs.execute(
            f"""REPLACE INTO
            LifetimeTech(region, tech, lifetime,
            notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
            VALUES('{region}', '{exs['tech']}', {lifetime},
            '(y) {aeo_note}', '{ref.id}', 1, 3, 2, 2, 3, '{utils.data_id(region)}')"""
        )

        # Some lighting techs didn't come around that long ago so restrict the oldest vintage
        if not pd.isna(exs['oldest_vint']): vints = [vint for vint in vints if vint >= exs['oldest_vint']]

        # Write existing data to database
        for v in range(len(vints)):

            vint = vints[v]
            weight = weights[v]

            if vint + lifetime <= config.model_periods[0]: continue

            exs_cap = existing_cap * weight

            note = (f"Ontario existing stock of residential bulb types by housing type (IESO, 2018) "
                    f"multiplied by housing stock by type (NRCan, {base_year}). "
                    f"Indexed to relative usage of bulb types by province versus Ontario (Statcan, 2018) "
                    f"and to projected population (Statcan) in {config.model_periods[0]}")
            ref = config.refs.add('lighting_existing_capacity',
                f"{config.params['lighting']['on_stock_ref']}; "
                f"{config.params['nrcan_reference']}; "
                f"{config.params['lighting']['usage_ref']}; "
                f"{config.params['aeo_reference']}"
            )
            curs.execute(
                f"""REPLACE INTO
                ExistingCapacity(region, tech, vintage, capacity, units,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{exs['tech']}', {vint}, {exs_cap}, '({lighting['cap_unit']})',
                '{note}', '{ref.id}', 1, 2, 3, 3, 4, '{utils.data_id(region)}')"""
            )
            
            ref = config.refs.get('aeo')
            curs.execute(
                f"""REPLACE INTO
                Efficiency(region, input_comm, tech, vintage, output_comm, efficiency,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{in_comm['comm']}', '{exs['tech']}', {vint}, '{lighting['comm']}', {exs['efficacy']},
                '({lighting['dem_unit']}/{in_comm['unit']}) {aeo_note}', '{ref.id}', 1, 2, 3, 3, 4, '{utils.data_id(region)}')"""
            )

            curs.execute(
                f"""REPLACE INTO
                LimitAnnualCapacityFactor(region, tech, vintage, output_comm, operator, factor,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{exs['tech']}', {vint}, '{lighting['comm']}', 'ge', {acf*0.95},
                '{min_note}', '{ref.id}', 1, 3, 2, 2, 4, '{utils.data_id(region)}')"""
            )
            curs.execute(
                f"""REPLACE INTO
                LimitAnnualCapacityFactor(region, tech, vintage, output_comm, operator, factor,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{exs['tech']}', {vint}, '{lighting['comm']}', 'le', {acf},
                '{acf_note}', '{ref.id}', 1, 3, 2, 2, 4, '{utils.data_id(region)}')"""
            )
            
            for period in config.model_periods:
                if vint > period or vint + lifetime <= period: continue

                curs.execute(
                    f"""REPLACE INTO
                    CostFixed(region, period, tech, vintage, cost, units,
                    notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                    VALUES('{region}', {period}, '{exs['tech']}', {vint}, {exs['cost_maintain']}, '(M$/{lighting['cap_unit']}.y)',
                    '{aeo_note}', '{ref.id}', 1, 2, 3, 3, 4, '{utils.data_id(region)}')"""
                )
    


    """
    ##############################################################
        New stock data
    ##############################################################
    """

    for code, aeo in aeo_techs.iterrows():

        if not aeo['include_new']: continue

        tech_desc = f"lighting - {aeo['description']}"
        curs.execute(
            f"""REPLACE INTO
            Technology(tech, flag, sector, annual, description, data_id)
            VALUES('{aeo['tech']}', 'p', 'residential', 1, '{tech_desc}', '{utils.data_id()}')"""
        )

        # Vintages for new stock are model periods
        for vint in config.model_periods:

            yr = utils.data_year(vint) # end-of-period data year for this vintage
            
            # Lifetime from aeo data converted from hours to years using the annual capacity factor and rounded
            lifetime = round(get_aeo_value(code, 'lamp_life', yr) * conv['lifetime'] / acf)

            ## LifetimeProcess
            # Using lifetime process because some bulb lives might improve over model periods in aeo data
            note = f"(y) AEO {yr} lamp life in hours divided by annual capacity factor (DOE, 2012)."
            ref = config.refs.get('aeo')
            curs.execute(
                f"""REPLACE INTO
                LifetimeProcess(region, tech, vintage, lifetime,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{aeo['tech']}', {vint}, {lifetime},
                '{note}', '{ref.id}', 1, 3, 3, 1, 2, '{utils.data_id(region)}')"""
            )
            
            ## Efficiency
            eff = conv['efficacy'] * get_aeo_value(code, 'efficacy', yr)
            curs.execute(
                f"""REPLACE INTO
                Efficiency(region, input_comm, tech, vintage, output_comm, efficiency,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{in_comm['comm']}', '{aeo['tech']}', {vint}, '{lighting['comm']}', {eff},
                '({lighting['dem_unit']}/{in_comm['unit']}) from AEO for {yr}', '{ref.id}', 1, 3, 3, 1, 2, '{utils.data_id(region)}')"""
            )
            
            ## CostInvest
            cost_invest = conv['cost'] * get_aeo_value(code, 'cost_install', yr)
            cost_invest = conv_curr(cost_invest)
            curs.execute(
                f"""REPLACE INTO
                CostInvest(region, tech, vintage, cost, units,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{aeo['tech']}', {vint}, {cost_invest}, '(M$/{lighting['cap_unit']})',
                'from AEO for {yr}', '{ref.id}', 1, 3, 3, 1, 2, '{utils.data_id(region)}')"""
            )
            
            for period in config.model_periods:
                
                # Can't pay for a technology if it can't exist
                if period < vint or vint + lifetime <= period: continue
                
                ## CostFixed
                cost_fixed = conv['cost'] * get_aeo_value(code, 'cost_maintain', yr)
                cost_fixed = conv_curr(cost_fixed)
                curs.execute(
                    f"""REPLACE INTO
                    CostFixed(region, period, tech, vintage, cost, units,
                    notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                    VALUES('{region}', {period}, '{aeo['tech']}', {vint}, {cost_fixed}, '(M$/{lighting['cap_unit']}.y)',
                    'from AEO for {yr}', '{ref.id}', 1, 3, 3, 1, 2, '{utils.data_id(region)}')"""
                )



    conn.commit()
    conn.close()



if __name__ == "__main__":
    
    aggregate()