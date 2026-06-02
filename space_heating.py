"""
Aggregates data for residential space heating
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
nrcan_ref = config.params['nrcan_reference']
statcan_year = config.params['statcan_data_year']
statcan_ref = config.params['statcan_reference']
fuel_commodities = config.fuel_commodities
nrcan_techs = config.existing_techs
space_heating = config.end_use_demands.loc['space heating']


def aggregate():

    for region in config.model_regions: aggregate_region(region)

    print(f"Space heating data aggregated into {os.path.basename(config.database_file)}\n")



def aggregate_region(region):

    # Connect to the new database file
    conn = sqlite3.connect(config.database_file)
    curs = conn.cursor() # Cursor object interacts with the sqlite db

    """
    ##############################################################
        Demand
    ##############################################################
    """

    note = (f"Sum of {base_year} secondary energy multiplied by efficiency per technology (NRCan, {base_year}). "
            "Dual fuel boilers taken to consume only first listed fuel in this calculation. "
            f"Indexed to projected population (Statcan, {statcan_year})")
    ref = config.refs.get('nrcan_statcan')

    # Table 8: Space Heating Secondary Energy Use by System Type
    t8_sec = utils.get_compr_db(region, 8, 3, 17)

    # Table 26: Heating System Stock Efficiencies
    t26_eff = utils.get_compr_db(region, 26, 2, 27) / 100 # to %

    # Multiply secondary energy by efficiency to get output heating energy
    # Dual fuel systems make this a little painful
    activity = t8_sec.copy()[base_year] # output primary energy (demand) by technology
    tracker = dict() # tracking multiples of the same fuel type
    for nrcan_tech in activity.index:

        fuel = nrcan_tech
        if "/" in fuel: fuel = fuel.split("/")[0]

        eff = t26_eff.loc[fuel, base_year]

        # To handle multple techs of the same fuel, take efficiencies from the table in order
        if fuel not in tracker.keys(): tracker[fuel] = 0
        if type(eff) is pd.Series:
            eff = eff.iloc[tracker[fuel]]
            tracker[fuel] += 1

        # Demanded primary energy is secondary input energy times efficiency
        # Dual fuel systems are assumed to consume the first listed fuel for simplicity
        activity.loc[nrcan_tech] *= eff

    # Index demand to population growth
    pop = config.populations[region]
    dem: pd.Series = activity.sum() * pop / pop.loc[base_year]

    # Write to database
    for period in config.model_periods:
        yr = utils.data_year(period)
        note = (
            f"Sum of {base_year} secondary energy multiplied by efficiency per technology (NRCan, {base_year}). "
            "Dual fuel boilers taken to consume only first listed fuel in this calculation. "
            f"Indexed to population projection at {yr} (Statcan, {statcan_year})"
        )
        curs.execute(
            f"""REPLACE INTO
            Demand(region, period, commodity, demand, units,
            notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
            VALUES('{region}', {period}, '{space_heating['comm']}', {dem.loc[yr].iloc[0]}, '({space_heating['dem_unit']})',
            '{note}', '{ref.id}', 1, 1, 1, 1, 3, '{utils.data_id(region)}')"""
        )



    """
    ##############################################################
        Efficiency of existing stock
    ##############################################################
    """

    ref = config.refs.get('nrcan')

    tracker = dict() # tracking multiples of the same nrcan fuel type
    for tech, row in config.existing_techs.iterrows():
        if row['end_use'] != 'space heating': continue

        # Get the NRCan nomenclature of the tech
        nrcan_stock = row['nrcan_stocks']
        if pd.isna(nrcan_stock): continue # skip empty rows

        ## Dual fuel boiler, needs two efficiencies
        # These are uncommon so making a lot of simplifications and only including existing
        if "/" in nrcan_stock:
            nrcan_fuels = nrcan_stock.split("/")
            fuels = row.loc['fuels'].split("+")

            # Add an efficiency for each fuel
            for f in [0,1]:
                fuel = nrcan_fuels[f]

                fuel = fuel.replace('Electric','Electricity') # annoying
                eff = t26_eff.loc[fuel, base_year]

                # To handle multple techs of the same fuel, take efficiencies from the table in order, (skipping one for wood, annoying)
                if fuel not in tracker.keys(): tracker[fuel] = 1 if fuel == 'Wood' else 0
                if type(eff) is pd.Series: # multiples exist
                    eff = eff.iloc[tracker[fuel]] # take the next in the list
                    tracker[fuel] += 1 # move up the tracker

                # Input commodity
                in_comm = fuel_commodities.loc[fuels[f]]
                note = f"({space_heating['dem_unit']}/{in_comm['unit']}) from NRCan in {base_year}"

                # Write dual fuel efficiencies to database
                for vint in config.tech_vints[tech]:
                    if vint + config.lifetimes[row['aeo_class']] <= config.model_periods[0]: continue

                    curs.execute(
                        f"""REPLACE INTO
                        Efficiency(region, input_comm, tech, vintage, output_comm, efficiency,
                        notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                        VALUES('{region}', '{in_comm['comm']}', '{tech}', {vint}, '{space_heating['comm']}', {eff},
                        '{note}', '{ref.id}', 1, 1, 1, 1, 3, '{utils.data_id(region)}')"""
                    )
    
            continue


        # Input commodity for others
        in_comm = fuel_commodities.loc[row.loc['fuels']]

        ## Technologies aggregated from multiple NRCan stocks
        if "+" in nrcan_stock:

            substocks = nrcan_stock.split("+")
            note = (
                f"({space_heating['dem_unit']}/{in_comm['unit']}) aggregated efficiencies "
                f"proportioned to secondary energy consumption in {base_year}"
            )

            sec_energy = np.array([t8_sec.loc[substock, base_year] for substock in substocks])
            effs = np.array([t26_eff.loc[substock, base_year] for substock in substocks])

            if sum(sec_energy) == 0:
                print(f"Existing space heating tech {tech} appears unused in {region}")
                continue

            # Efficiency is aggregated by ratios of secondary energy consumption in data year
            # This matches the demand calculation which is efficiency multiplied by secondary energy consumption
            eff = np.dot(effs, sec_energy) / sum(sec_energy)

        ## Single fuel and single stock technologies    
        else:
            note = f"({space_heating['dem_unit']}/{in_comm['unit']}) from NRCan in {base_year}"
            eff = t26_eff.loc[nrcan_stock, base_year]
            if type(eff) is pd.Series: eff = eff.iloc[0] # these technologies are above dual fuel boilers in the table


        # Write single fuel efficiencies to database
        for vint in config.tech_vints[tech]:
            if vint + config.lifetimes[row['aeo_class']] <= config.model_periods[0]: continue
            
            curs.execute(
                f"""REPLACE INTO
                Efficiency(region, input_comm, tech, vintage, output_comm, efficiency,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{in_comm['comm']}', '{tech}', {vint}, '{space_heating['comm']}', {eff},
                '{note}', '{ref.id}', 1, 1, 1, 1, 3, '{utils.data_id(region)}')"""
            )
    


    """
    ##############################################################
        Existing Capacity and Annual Capacity Factor
    ##############################################################
    """

    # Table 21: Heating System Stock by Building Type and Heating System Type
    t21_stk = utils.get_compr_db(region, 21, 16, 30) # kunit

    # Notes for database
    note = (
        f"{base_year} stock (NRCan, {base_year}) carried forward to last existing vintage"
         " and distributed evenly over feasible existing vintages."
    )

    # Get existing capacities from NRCan stock and distribute over past vintages
    for tech, row in nrcan_techs.iterrows():
        if row['end_use'] != 'space heating': continue

        nrcan_stocks = row.loc['nrcan_stocks']
        if pd.isna(nrcan_stocks): continue # no existing stock from NRCan

        substocks = nrcan_stocks.split("+")


        ## Existing capacity
        # Get existing capacity (stock) from nrcan and index to population growth
        existing_cap = sum([t21_stk.loc[substock, base_year] for substock in substocks])
        if existing_cap == 0:
            print(f"No existing capacity for space heating tech {tech} in region {region}. Skipped.")
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
                VALUES('{region}', '{tech}', {vint}, {exs_cap}, '({space_heating['cap_unit']})',
                '{note}', '{ref.id}', 1, 1, 1, 1, 3, '{utils.data_id(region)}')"""
            )
        

        ## Annual capacity factor for NRCan existing stock
        # (for new stock pulled in all sectors post processing)
        max_note = (
            "Annual utilisation of units. (annual secondary energy consumption * efficiency) "
            f"/ (c2a * existing stock) (NRCan, {base_year})"
        )
        min_note = "95% of MaxACF. " + max_note

        act = sum([activity.loc[substock] for substock in substocks]) # annual PJ output
        c2a = space_heating['c2a']

        # Annual capacity factor is actual annual activity divided by max possible annual activity from arbitrary c2a
        acf = act / (existing_cap * c2a)

        for vint in vints:
            if vint + config.lifetimes[row['aeo_class']] <= config.model_periods[0]: continue

            curs.execute(
                f"""REPLACE INTO
                LimitAnnualCapacityFactor(region, tech, vintage, output_comm, operator, factor,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{tech}', {vint}, '{space_heating['comm']}', 'ge', {acf*0.95},
                '{min_note}', '{ref.id}', 1, 1, 1, 1, 3, '{utils.data_id(region)}')"""
            )
            curs.execute(
                f"""REPLACE INTO
                LimitAnnualCapacityFactor(region, tech, vintage, output_comm, operator, factor,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{tech}', {vint}, '{space_heating['comm']}', 'le', {acf},
                '{max_note}', '{ref.id}', 1, 1, 1, 1, 3, '{utils.data_id(region)}')"""
            )

    conn.commit()
    conn.close()

    if config.params['include_furnace_fans']: aggregate_furnace_fans(region)



def aggregate_furnace_fans(region):

    # Connect to the new database file
    conn = sqlite3.connect(config.database_file)
    curs = conn.cursor() # Cursor object interacts with the sqlite db


    """
    ##############################################################
        Furnace fan electricity consumption
    ##############################################################
    """

    frn_fan = config.params['furnace_fans'] # parameters relating to furnace fans
    eff = frn_fan['efficiency']
    elc_comm = config.fuel_commodities.loc['electricity']
    split = eff * frn_fan['output_split'] / (1 + eff * frn_fan['output_split']) # calculating TOS to achieve correct electricity consumption
    tos_note = f"({elc_comm['unit']}/{space_heating['dem_unit']}) Furnace fan electricity consumption. x/(1+x) where x is assumed 6 kWh into fan / MMBtu out (nyserda, 2013)."

    # Get technologies that need furnace fan consumption (fan tag in AEO data and space heating end use)
    fan_classes = config.aeo_res_class.loc[config.aeo_res_class['Furnace Fan Flag']==1].index.unique()
    fan_techs = [
        *config.existing_techs.loc[
            (config.existing_techs['aeo_class'].isin(fan_classes))
            & (config.existing_techs['end_use'] == 'space heating')
        ].index.values,
        *config.new_techs.loc[
            (config.new_techs['aeo_class'].isin(fan_classes))
            & (config.new_techs['end_uses'].str.contains('space heating'))
            & (config.new_techs['include_new'])
        ].index.values
    ]

    ref = config.refs.add('furnace_fans', frn_fan['reference'])

    for tech in fan_techs:
        
        vints = config.tech_vints[tech]
        if tech in nrcan_techs.index: life = config.lifetimes[nrcan_techs.loc[tech, 'aeo_class']]
        else: life = config.lifetimes[config.new_techs.loc[tech, 'aeo_class']]

        # Add a dummy process to convert input fan electricity to worthless dummy commodity
        # 100% efficiency so TechOutputSplit can be used
        for vint in vints:
            if vint + life <= config.model_periods[0]: continue

            curs.execute(
                f"""REPLACE INTO
                Efficiency(region, input_comm, tech, vintage, output_comm, efficiency, notes, data_id)
                VALUES('{region}', '{elc_comm['comm']}', '{tech}', {vint}, '{elc_comm['comm']}', {eff},
                'arbitrarily small non-zero efficiency', '{utils.data_id(region)}')"""
            )
        
        # Set ratio of fan electricity consumption to output heat
        for period in config.model_periods:
            if max(vints) + life <= period: continue

            curs.execute(
                f"""REPLACE INTO
                LimitTechOutputSplit(region, period, tech, output_comm, operator, proportion,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', {period}, '{tech}', '{elc_comm['comm']}', {split},
                '{tos_note}', '{ref.id}', 4, 3, 3, 3, 4, '{utils.data_id(region)}')"""
            )
    

    conn.commit()
    conn.close()



if __name__ == "__main__":
    
    aggregate()