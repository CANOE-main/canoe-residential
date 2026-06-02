"""
Aggregates data for residential water heating
Written by Ian David Elder for the CANOE model
"""

import utils
import os
import sqlite3
from setup import config

# Shortens lines a bit
base_year = config.params['base_year']
aeo_year = config.params['aeo_data_year']
statcan_year = config.params['statcan_data_year']
fuel_commodities = config.fuel_commodities
nrcan_techs = config.existing_techs
aeo_techs = config.new_techs
aeo_res_class = config.aeo_res_class
aeo_res_equip = config.aeo_res_equip
water_heating = config.end_use_demands.loc['water heating']



def aggregate():

    for region in config.model_regions: aggregate_region(region)
    
    print(f"Water heating data aggregated into {os.path.basename(config.database_file)}\n")



def aggregate_region(region):

    # Connect to the new database file
    conn = sqlite3.connect(config.database_file)
    curs = conn.cursor() # Cursor object interacts with the sqlite db



    """
    ##############################################################
        Efficiency of existing stock
    ##############################################################
    """

    stock_effs = dict() # track efficiencies by nrcan stock
    ref = config.refs.get('aeo')

    for tech, row in config.existing_techs.iterrows():
        if row['end_use'] != 'water heating': continue

        # Input commodity
        in_comm = fuel_commodities.loc[row.loc['fuels']]

        note = f"({water_heating['dem_unit']}/{in_comm['unit']}) base efficiency fpr {row['aeo_class']}"

        # Taking efficiency from base efficiency of AEO class - not great but it'll do
        eff = aeo_res_class.loc[row['aeo_class'], 'Base Efficiency']
        stock_effs[row['nrcan_stocks']] = eff

        # Write single fuel efficiencies to database
        for vint in config.tech_vints[tech]:
            if vint + config.lifetimes[row['aeo_class']] <= config.model_periods[0]: continue

            curs.execute(
                f"""REPLACE INTO
                Efficiency(region, input_comm, tech, vintage, output_comm, efficiency,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{in_comm['comm']}', '{tech}', {vint}, '{water_heating['comm']}', {eff},
                '{note}', '{ref.id}', 1, 2, 3, 1, 3, '{utils.data_id(region)}')"""
            )
            


    """
    ##############################################################
        Demand
    ##############################################################
    """


    ref = config.refs.get('nrcan_statcan')

    # Table 10: Water Heating Secondary Energy Use and GHG Emissions by Energy Source
    t10_sec = utils.get_compr_db(region, 10, 3, 7)

    # Activity (PJ output) is secondary energy times efficiency, and demand is sum of activity
    activity = t10_sec.copy()
    for nrcan_stock, row in activity.iterrows():
        row *= stock_effs[nrcan_stock]

    # Index demand to population growth
    pop = config.populations[region]
    dem = activity[base_year].sum() * pop / pop.loc[base_year]

    # Write to database
    for period in config.model_periods:
        yr = utils.data_year(period)
        note = (
            f"Sum of {base_year} secondary energy multiplied by efficiency per technology (NRCan, {base_year}). "
            f"Indexed to projected populationin {yr} (Statcan, {statcan_year})"
        )
        curs.execute(
            f"""REPLACE INTO
            Demand(region, period, commodity, demand, units,
            notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
            VALUES('{region}', {period}, '{water_heating['comm']}', {dem.loc[yr].iloc[0]}, '({water_heating['dem_unit']})',
            '{note}', '{ref.id}', 1, 1, 3, 1, 3, '{utils.data_id(region)}')"""
        )

    

    """
    ##############################################################
        Existing Capacity and Annual Capacity Factor
    ##############################################################
    """

    # Table 28: Water Heater Stock by Building Type and Energy Source
    t28_stk = utils.get_compr_db(region, 28, 15, 20) # kunit

    # Notes for database
    note = (
        f"{base_year} stock (NRCan, {base_year}) carried forward to last existing vintage"
         " and distributed evenly over feasible existing vintages."
    )
    ref = config.refs.get('nrcan')

    # Get existing capacities from NRCan stock and distribute over past vintages
    for tech, row in nrcan_techs.iterrows():
        if row['end_use'] != 'water heating': continue

        nrcan_stock = row.loc['nrcan_stocks']

        ## Existing capacity
        # Get existing capacity (stock) from nrcan and index to population growth
        existing_cap = t28_stk.loc[nrcan_stock, base_year]

        if existing_cap == 0:
            print(f"No existing capacity for water heating tech {tech} in region {region}. Skipped.")
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
                VALUES('{region}', '{tech}', {vint}, {exs_cap}, '({water_heating['cap_unit']})',
                '{note}', '{ref.id}', 1, 1, 2, 1, 1, '{utils.data_id(region)}')"""
            )
        

        ## Annual capacity factor for NRCan existing stock
        # (for new stock pulled in all sectors post processing)
        max_note = (
            "Annual utilisation of units. (annual secondary energy consumption * efficiency) "
            f"/ (c2a * existing stock) (NRCan, {base_year})"
        )
        min_note = "95% of MaxACF for slack. " + max_note

        act = activity[base_year].loc[nrcan_stock] # annual PJ output
        c2a = config.end_use_demands.loc['water heating', 'c2a']

        # Annual capacity factor is actual annual activity divided by max possible annual activity from arbitrary c2a
        acf = act / (existing_cap * c2a)

        for vint in vints:
            if vint + config.lifetimes[row['aeo_class']] <= config.model_periods[0]: continue

            curs.execute(
                f"""REPLACE INTO
                LimitAnnualCapacityFactor(region, tech, vintage, output_comm, operator, factor,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{tech}', {vint}, '{water_heating['comm']}', 'ge', {acf*0.95},
                '{min_note}', '{ref.id}', 1, 1, 2, 1, 3, '{utils.data_id(region)}')"""
            )
            curs.execute(
                f"""REPLACE INTO
                LimitAnnualCapacityFactor(region, tech, vintage, output_comm, operator, factor,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{tech}', {vint}, '{water_heating['comm']}', 'le', {acf},
                '{max_note}', '{ref.id}', 1, 1, 2, 1, 3, '{utils.data_id(region)}')"""
            )


    conn.commit()
    conn.close()



if __name__ == "__main__":
    
    aggregate()