"""
Aggregates data for residential water heating
Written by Ian David Elder for the CANOE model
"""

import canoe_residential.utils as utils
import canoe_residential.nrcan as nrcan
import os
import sqlite3
from canoe_schema.v3_2 import models as schema_models
from canoe_residential.setup import config

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

            sql, params = schema_models.Efficiency(
                region=region,
                input_comm=in_comm['comm'],
                tech=tech,
                vintage=vint,
                output_comm=water_heating['comm'],
                efficiency=eff,
                notes=note,
                data_source=ref.id,
                dq_cred=1,
                dq_geog=2,
                dq_struc=3,
                dq_tech=1,
                dq_time=3,
                data_id=utils.data_id(region),
            ).to_replace_sql()
            curs.execute(sql, params)
            


    """
    ##############################################################
        Demand
    ##############################################################
    """


    ref = config.refs.get('nrcan_statcan')

    # Table 10: Water Heating Secondary Energy Use and GHG Emissions by Energy Source
    t10_sec = nrcan.get_compr_db(region, 10, 3, 7)

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
        sql, params = schema_models.Demand(
            region=region,
            period=period,
            commodity=water_heating['comm'],
            demand=dem.loc[yr].iloc[0],
            units=f"({water_heating['dem_unit']})",
            notes=note,
            data_source=ref.id,
            dq_cred=1,
            dq_geog=1,
            dq_struc=3,
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

    # Table 28: Water Heater Stock by Building Type and Energy Source
    t28_stk = nrcan.get_compr_db(region, 28, 15, 20) # kunit

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

            sql, params = schema_models.ExistingCapacity(
                region=region,
                tech=tech,
                vintage=vint,
                capacity=exs_cap,
                units=f"({water_heating['cap_unit']})",
                notes=note,
                data_source=ref.id,
                dq_cred=1,
                dq_geog=1,
                dq_struc=2,
                dq_tech=1,
                dq_time=1,
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
        c2a = config.end_use_demands.loc['water heating', 'c2a']

        # Annual capacity factor is actual annual activity divided by max possible annual activity from arbitrary c2a
        acf = act / (existing_cap * c2a)

        for vint in vints:
            if vint + config.lifetimes[row['aeo_class']] <= config.model_periods[0]: continue

            sql, params = schema_models.LimitAnnualCapacityFactor(
                region=region,
                tech=tech,
                vintage=vint,
                output_comm=water_heating['comm'],
                operator='ge',
                factor=acf * 0.95,
                notes=min_note,
                data_source=ref.id,
                dq_cred=1,
                dq_geog=1,
                dq_struc=2,
                dq_tech=1,
                dq_time=3,
                data_id=utils.data_id(region),
            ).to_replace_sql()
            curs.execute(sql, params)
            sql, params = schema_models.LimitAnnualCapacityFactor(
                region=region,
                tech=tech,
                vintage=vint,
                output_comm=water_heating['comm'],
                operator='le',
                factor=acf,
                notes=max_note,
                data_source=ref.id,
                dq_cred=1,
                dq_geog=1,
                dq_struc=2,
                dq_tech=1,
                dq_time=3,
                data_id=utils.data_id(region),
            ).to_replace_sql()
            curs.execute(sql, params)


    conn.commit()
    conn.close()



if __name__ == "__main__":
    
    aggregate()