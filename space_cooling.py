"""
Aggregates data for residential space cooling
Written by Ian David Elder for the CANOE model
"""

import utils
import pandas as pd
import os
import numpy as np
import sqlite3
from setup import config

# Shortens lines a bit
base_year = config.params['base_year']
statcan_year = config.params['statcan_data_year']
fuel_commodities = config.fuel_commodities
nrcan_techs = config.existing_techs
space_cooling = config.end_use_demands.loc['space cooling']


def aggregate():

    for region in config.model_regions: aggregate_region(region)
    
    print(f"Space cooling data aggregated into {os.path.basename(config.database_file)}\n")



def aggregate_region(region):

    # Connect to the new database file
    conn = sqlite3.connect(config.database_file)
    curs = conn.cursor() # Cursor object interacts with the sqlite db

    """
    ##############################################################
        Demand
    ##############################################################
    """
    
    ref = config.refs.get('nrcan_statcan')

    # Table 4: Space Cooling Secondary Energy Use and GHG Emissions by Cooling System Type
    t4_sec = utils.get_compr_db(region, 4, 3, 4)

    # Table 27: Cooling System Stock by Type, New Unit Efficiencies, Stock Efficiencies and Unit Capacity Ratio
    t27_stk_eff = utils.get_compr_db(region, 27, 15, 16)
    t27_stk_eff.index=t4_sec.index
    t27_stk_eff *= config.params['conversion_factors']['efficiency']['EER']

    # Activity (PJ output) is secondary energy times efficiency, and demand is sum of activity
    activity = t4_sec.values * t27_stk_eff.values
    activity = pd.DataFrame(data=activity, columns=t4_sec.columns, index=t4_sec.index)

    # Index demand to population growth
    pop = config.populations[region]
    dem = activity[base_year].sum() * pop / pop.loc[base_year]

    # Write to database
    for period in config.model_periods:
        yr = utils.data_year(period)
        note = (
            f"Sum of {base_year} secondary energy multiplied by efficiency per technology (NRCan, {base_year}). "
            f"Indexed to projected population in {yr} (Statcan)"
        )
        curs.execute(
            f"""REPLACE INTO
            Demand(region, period, commodity, demand, units,
            notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
            VALUES('{region}', {period}, '{space_cooling['comm']}', {dem.loc[yr].iloc[0]}, '({space_cooling['dem_unit']})',
            '{note}', '{ref.id}', 1, 1, 1, 1, 3, '{utils.data_id(region)}')"""
        )



    """
    ##############################################################
        Efficiency of existing stock
    ##############################################################
    """

    ref = config.refs.get('nrcan')

    for tech, row in config.existing_techs.iterrows():
        if row['end_use'] != 'space cooling': continue

        # Input commodity
        in_comm = fuel_commodities.loc[row.loc['fuels']]

        note = f"({space_cooling['dem_unit']}/{in_comm['unit']}) new build efficiency per vintage"

        # Get the NRCan nomenclature of the tech
        nrcan_stock = row['nrcan_stocks']

        # Write single fuel efficiencies to database
        for vint in config.tech_vints[tech]:
            if vint + config.lifetimes[row['aeo_class']] <= config.model_periods[0]: continue
            
            # Efficiency is new build efficiency for that year, or 2020 at the latest
            eff = t27_stk_eff.loc[nrcan_stock, min(vint, max(np.array(t27_stk_eff.columns, dtype=int)))]

            curs.execute(
                f"""REPLACE INTO
                Efficiency(region, input_comm, tech, vintage, output_comm, efficiency,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{in_comm['comm']}', '{tech}', {vint}, '{space_cooling['comm']}', {eff},
                '{note}', '{ref.id}', 1, 1, 1, 1, 3, '{utils.data_id(region)}')"""
            )

    

    """
    ##############################################################
        Existing Capacity and Annual Capacity Factor
    ##############################################################
    """

    # Existing cooling stock from NRCan data
    t27_stk = utils.get_compr_db(region, 27, 3, 4) # kunit

    # Notes for database
    note = (
        f"{base_year} stock (NRCan, {base_year}) carried forward to last existing vintage"
         " and distributed evenly over feasible existing vintages."
    )

    # Get existing capacities from NRCan stock and distribute over past vintages
    for tech, row in nrcan_techs.iterrows():
        if row['end_use'] != 'space cooling': continue

        nrcan_stock = row.loc['nrcan_stocks']


        ## Existing capacity
        # Get existing capacity (stock) from nrcan and index to population growth
        existing_cap = t27_stk.loc[nrcan_stock, base_year]

        if existing_cap == 0:
            print(f"No existing capacity for space cooling tech {tech} in region {region}. Skipped.")
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
                VALUES('{region}', '{tech}', {vint}, {exs_cap}, '({space_cooling['cap_unit']})',
                '{note}', '{ref.id}', 1, 1, 1, 1, 3, '{utils.data_id(region)}')"""
            )


        ## Annual capacity factor for NRCan existing stock
        # (for new stock pulled in all sectors post processing)
        max_note = (
            "Annual utilisation of units. (annual secondary energy consumption * efficiency) "
            f"/ (c2a * existing stock) (NRCan, {base_year})"
        )
        min_note = "95% of MaxACF for slack. " + max_note

        act = activity[base_year].loc[nrcan_stock] # annual PJ output
        c2a = config.end_use_demands.loc['space cooling', 'c2a']

        # Annual capacity factor is actual annual activity divided by max possible annual activity from arbitrary c2a
        acf = act / (existing_cap * c2a)

        for vint in vints:
            if vint + config.lifetimes[row['aeo_class']] <= config.model_periods[0]: continue
            
            curs.execute(
                f"""REPLACE INTO
                LimitAnnualCapacityFactor(region, tech, vintage, output_comm, operator, factor,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{tech}', {vint}, '{space_cooling['comm']}', 'ge', {acf*0.95},
                '{min_note}', '{ref.id}', 1, 1, 1, 1, 3, '{utils.data_id(region)}')"""
            )
            curs.execute(
                f"""REPLACE INTO
                LimitAnnualCapacityFactor(region, tech, vintage, output_comm, operator, factor,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{tech}', {vint}, '{space_cooling['comm']}', 'le', {acf},
                '{max_note}', '{ref.id}', 1, 1, 1, 1, 3, '{utils.data_id(region)}')"""
            )



    conn.commit()
    conn.close()



if __name__ == "__main__":
    
    aggregate()