"""
Aggregates data for residential space cooling
Written by Ian David Elder for the CANOE model
"""

import canoe_residential.utils as utils
import canoe_residential.nrcan as nrcan
import pandas as pd
import os
import numpy as np
import sqlite3
from canoe_schema.v3_2 import models as schema_models
from canoe_residential.setup import config

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
    t4_sec = nrcan.get_compr_db(region, 4, 3, 4)

    # Table 27: Cooling System Stock by Type, New Unit Efficiencies, Stock Efficiencies and Unit Capacity Ratio
    t27_stk_eff = nrcan.get_compr_db(region, 27, 15, 16)
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
        sql, params = schema_models.Demand(
            region=region,
            period=period,
            commodity=space_cooling['comm'],
            demand=dem.loc[yr].iloc[0],
            units=f"({space_cooling['dem_unit']})",
            notes=note,
            data_source=ref.id,
            dq_cred=1,
            dq_geog=1,
            dq_struc=1,
            dq_tech=1,
            dq_time=3,
            data_id=utils.data_id(region),
        ).to_replace_sql()
        curs.execute(sql, params)



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

            sql, params = schema_models.Efficiency(
                region=region,
                input_comm=in_comm['comm'],
                tech=tech,
                vintage=vint,
                output_comm=space_cooling['comm'],
                efficiency=eff,
                notes=note,
                data_source=ref.id,
                dq_cred=1,
                dq_geog=1,
                dq_struc=1,
                dq_tech=1,
                dq_time=3,
                data_id=utils.data_id(region),
            ).to_replace_sql()
            curs.execute(sql, params)

    

    """
    ##############################################################
        Existing Capacity and Annual Capacity Factor
    ##############################################################
    """

    # Existing cooling stock from NRCan data
    t27_stk = nrcan.get_compr_db(region, 27, 3, 4) # kunit

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

            sql, params = schema_models.ExistingCapacity(
                region=region,
                tech=tech,
                vintage=vint,
                capacity=exs_cap,
                units=f"({space_cooling['cap_unit']})",
                notes=note,
                data_source=ref.id,
                dq_cred=1,
                dq_geog=1,
                dq_struc=1,
                dq_tech=1,
                dq_time=3,
                data_id=utils.data_id(region),
            ).to_replace_sql()
            curs.execute(sql, params)


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
            
            sql, params = schema_models.LimitAnnualCapacityFactor(
                region=region,
                tech=tech,
                vintage=vint,
                output_comm=space_cooling['comm'],
                operator='ge',
                factor=acf * 0.95,
                notes=min_note,
                data_source=ref.id,
                dq_cred=1,
                dq_geog=1,
                dq_struc=1,
                dq_tech=1,
                dq_time=3,
                data_id=utils.data_id(region),
            ).to_replace_sql()
            curs.execute(sql, params)
            sql, params = schema_models.LimitAnnualCapacityFactor(
                region=region,
                tech=tech,
                vintage=vint,
                output_comm=space_cooling['comm'],
                operator='le',
                factor=acf,
                notes=max_note,
                data_source=ref.id,
                dq_cred=1,
                dq_geog=1,
                dq_struc=1,
                dq_tech=1,
                dq_time=3,
                data_id=utils.data_id(region),
            ).to_replace_sql()
            curs.execute(sql, params)



    conn.commit()
    conn.close()



if __name__ == "__main__":
    
    aggregate()