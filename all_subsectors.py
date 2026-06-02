"""
Aggregates residential non-subsector-specific data
Written by Ian David Elder for the CANOE model
"""

from setup import config
import utils
import pandas as pd
from scipy.special import gamma
import sqlite3
import os
import space_heating
import space_cooling
import water_heating
import lighting
import appliances
from matplotlib import pyplot as pp
import weather_mapping
from currency_conversion import conv_curr

# Shortens lines a bit
nrcan_techs = config.existing_techs
aeo_techs = config.new_techs
aeo_res_class = config.aeo_res_class
aeo_res_equip = config.aeo_res_equip
fuel_commodities = config.fuel_commodities
end_use_demands = config.end_use_demands
aeo_year = config.params['aeo_data_year']
base_year = config.params['base_year']
conversion_factors = config.params['conversion_factors']



def aggregate():

    print("Aggregating sub-sector data...\n")

    pre_process()
    
    ## Aggregate subsectors
    space_heating.aggregate()
    space_cooling.aggregate()
    water_heating.aggregate()
    lighting.aggregate()
    appliances.aggregate()

    if config.params['include_dsd']: aggregate_dsd()
    if config.params['include_emissions']: aggregate_emissions()
    # if config.params['include_imports']: aggregate_imports() # no longer supported

    post_process()

    cleanup()

    print(f"Sub-sector data aggregated into {os.path.basename(config.database_file)}\n")



# For non-regional aggregation
def pre_process():

    # Connect to the new database file
    conn = sqlite3.connect(config.database_file)
    curs = conn.cursor() # Cursor object interacts with the sqlite db


    """
    ##############################################################
        Basic parameters
    ##############################################################
    """

    for season in config.time['season'].unique():
        curs.execute(
            f"""REPLACE INTO
            SeasonLabel(season)
            VALUES('{season}')"""
        )

    for period in config.model_periods:
        for i, season in enumerate(config.time['season'].unique()):
            curs.execute(
                f"""REPLACE INTO
                TimeSeason(period, sequence, season)
                VALUES({period}, {i}, '{season}')"""
            )

        for _h, row in config.time.iterrows():
            curs.execute(
                f"""REPLACE INTO
                TimeSegmentFraction(period, season, tod, segfrac)
                VALUES({period}, '{row['season']}', '{row['tod']}', {1/8760})"""
            )

    for i, tod in enumerate(config.time['tod'].unique()):
        curs.execute(
            f"""REPLACE INTO
            TimeOfDay(sequence, tod)
            VALUES({i}, '{tod}')"""
        )
        
    for i, period in enumerate([*config.model_periods, config.model_periods[-1] + config.params['period_step']]):
        curs.execute(
            f"""REPLACE INTO
            TimePeriod(sequence, period, flag)
            VALUES({i}, {period}, 'f')"""
        )

    for region, row in config.regions[config.regions['include']].iterrows():
        curs.execute(
            f"""REPLACE INTO
            Region(region, notes)
            VALUES('{region}', '{row['description']}')"""
        )


    """
    ##############################################################
        Commodities
    ##############################################################
    """

    for _fuel, row in fuel_commodities.iterrows():
        curs.execute(
            f"""REPLACE INTO
            Commodity(name, flag, description, data_id)
            VALUES('{row['comm']}', '{row['flag']}', '(PJ) {row['description']}', '{utils.data_id()}')"""
        )
    for _end_use, row in end_use_demands.iterrows():
        curs.execute(
            f"""REPLACE INTO
            Commodity(name, flag, description, data_id)
            VALUES('{row['comm']}', 'd', '(PJ) {row['description']}', '{utils.data_id()}')"""
        )
    
    if config.params['include_emissions']:
        # CO2-equivalent emission commodity
        curs.execute(
            f"""REPLACE INTO
            Commodity(name, flag, description, data_id)
            VALUES('{config.params['emission_commodity']}', 'e', '(ktCO2eq) CO2-equivalent emissions', '{utils.data_id()}')"""
        )



    """
    ##############################################################
        Technologies, lifetimes, tech-vintage pairs
    ##############################################################
    """

    ##############################################################
    # Lifetimes
    ##############################################################

    for aeo_class in aeo_res_class.index:

        # Get lifetime from mean of weibull distribution
        weibull_k = aeo_res_class.loc[aeo_class, 'Weibull K']
        weibull_l = aeo_res_class.loc[aeo_class, 'Weibull λ']

        # There are double ups of class for heat pumps because two end uses
        if type(weibull_k) is pd.Series: weibull_k = weibull_k.iloc[0]
        if type(weibull_l) is pd.Series: weibull_l = weibull_l.iloc[0]

        # Calculate lifetime and add to lifetimes dictionary
        lifetime = round(weibull_l * gamma(1 + 1/weibull_k)) # mean of weibull distribution
        config.lifetimes[aeo_class] = lifetime


    ##############################################################
    # AEO data (new)
    ##############################################################

    for tech, row in aeo_techs.iterrows():

        if not row['include_new']: continue
        
        tech_desc = f"{row.loc['end_uses']} - {row.loc['description']}"
        curs.execute(
            f"""REPLACE INTO
            Technology(tech, flag, sector, description, data_id)
            VALUES('{tech}', 'p', 'residential', '{tech_desc}', '{utils.data_id()}')"""
        )

        for flag in row['flags'].split(','):
            curs.execute(
                f"""UPDATE Technology
                SET {flag} == 1
                WHERE tech == '{tech}'"""
            )

        # Add future vintages to vintage dictionary
        config.tech_vints[tech] = config.model_periods


    ##############################################################
    # NRCan data (existing)
    ##############################################################

    for tech, row in nrcan_techs.iterrows():

        tech_desc = f"{row.loc['end_use']} - {row.loc['description']}"

        curs.execute(
            f"""REPLACE INTO
            Technology(tech, flag, sector, description, data_id)
            VALUES('{tech}', 'p', 'residential', '{tech_desc}', '{utils.data_id()}')"""
        )

        for flag in row['flags'].split(','):
            curs.execute(
                f"""UPDATE Technology
                SET {flag} == 1
                WHERE tech == '{tech}'"""
            )

        # Get equivalent future tech
        aeo_class = row.loc['aeo_class']
        if pd.isna(aeo_class): continue # should only apply to appliances other

        # Add lifetimes and feasible vintages to config dictionaries
        exs_vints, _weights = utils.stock_vintages(config.lifetimes[aeo_class])
        config.tech_vints[tech] = exs_vints


    conn.commit()
    conn.close()

    for region in config.model_regions: pre_aggregate_region(region)

    print(f"Pre aggregation complete.\n")
        


# For region-specific aggregation
def pre_aggregate_region(region):

    # Connect to the new database file
    conn = sqlite3.connect(config.database_file)
    curs = conn.cursor() # Cursor object interacts with the sqlite db

    """
    ##############################################################
        Efficiency, CostInvest, CostFixed
    ##############################################################
    """

    cens_div = config.regions.loc[region, 'us_census_div']

    ref = config.refs.get('aeo')
    ref_updated = config.refs.add('aeo_updated', config.params['aeo_updated_reference'])

    # Narrow down the dataframe with each nested section to speed things up
    df0 = aeo_res_equip.loc[(aeo_res_equip['Census Division'] == cens_div) | (aeo_res_equip['Census Division'] == 11)]


    ##############################################################
    # AEO data (new)
    ##############################################################

    # All technologies from aeo technologies input csv
    for tech, row in aeo_techs.iterrows():

        if not row['include_new']: continue

        aeo_class = row['aeo_class']
        aeo_equip = row['aeo_equip']

        in_comm = fuel_commodities.loc[row['fuel'], 'comm']
        lifetime = config.lifetimes[aeo_class]

        ## LifetimeTech
        note = f'(y) Mean of Weibull distribution for {aeo_class}'
        ref = config.refs.get('aeo')
        curs.execute(
            f"""REPLACE INTO
            LifetimeTech(region, tech, lifetime,
            notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
            VALUES('{region}', '{tech}', {lifetime},
            '{note}', '{ref.id}', 1, 2, 2, 2, 3, '{utils.data_id(region)}')"""
        )

        if type(df0) is pd.DataFrame: df1 = df0.loc[aeo_equip]
        elif type(df0) is pd.Series: df1 = df0 # only one row remaining

        end_uses = row.loc['end_uses'].split("+")
        end_use_ids = row.loc['end_use_ids'].split("+")

        cap_unit = end_use_demands.loc[end_uses[0], 'cap_unit']

        # All future periods are valid vintages
        for vint in config.tech_vints[tech]:

            yr = utils.data_year(vint) # end-of-period data year for this vintage
            
            if type(df1) is pd.DataFrame:
                df2 = df1.loc[(df1['First Year']<=yr) & (yr<=df1['Last Year'])]
                cost_invest = df2.loc[df2['Replacement Cost'] != 0]['Replacement Cost'].iloc[0]
            elif type(df1) is pd.Series:
                df2 = df1 # only one row remaining
                cost_invest = df2['Replacement Cost']
            
            # AEO table splits heat pump costs between heating and cooling, annoyingly
            if row.loc['end_uses'] == 'space heating+space cooling': cost_invest *= 2


            ## CostInvest
            cost_invest *= config.params['conversion_factors']['cost']['invest']
            cost_invest = conv_curr(cost_invest)

            curs.execute(
                f"""REPLACE INTO
                CostInvest(region, tech, vintage, cost, units,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{region}', '{tech}', {vint}, {cost_invest}, '(M$/{cap_unit})',
                '{yr} replacement cost for {aeo_equip}', '{ref.id}', 1, 2, 2, 2, 3, '{utils.data_id(region)}')"""
            )
            

            ## CostFixed
            cost_fixed = row['cost_fixed'] * config.params['conversion_factors']['cost']['fixed']
            cost_fixed = conv_curr(cost_fixed)

            if cost_fixed != 0:
                for period in config.model_periods:

                    if period < vint or vint + lifetime <= period: continue

                    curs.execute(
                        f"""REPLACE INTO
                        CostFixed(region, period, tech, vintage, cost, units,
                        notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                        VALUES('{region}', {period}, '{tech}', {vint}, {cost_fixed}, '(M$/{cap_unit}.y)',
                        '{yr} Fixed O&M cost for {aeo_equip}', '{ref_updated.id}', 1, 2, 2, 2, 3, '{utils.data_id(region)}')"""
                    )


            # For each end use the technology supplies (heat pumps do heating and cooling)
            for e in range(len(end_uses)):

                end_use = end_uses[e]
                end_use_id = int(end_use_ids[e])

                out_comm = end_use_demands.loc[end_use, 'comm']
                
                if type(df2) is pd.DataFrame: eff = df2.loc[(df2['End Use'] == int(end_use_id))]['Efficiency'].iloc[0]
                elif type(df2) is pd.Series: eff = df2['Efficiency'] # only one row remaining

                eff_metric = aeo_res_class.loc[aeo_res_class['End Use'] == int(end_use_id)].loc[aeo_class, 'Efficiency Metric']
                if type(eff_metric) is pd.Series: eff_metric = eff_metric.iloc[0]
                if eff_metric in config.params['conversion_factors']['efficiency'].keys(): eff *= config.params['conversion_factors']['efficiency'][eff_metric]

                ## Default Efficiency
                curs.execute(
                    f"""REPLACE INTO
                    Efficiency(region, input_comm, tech, vintage, output_comm, efficiency,
                    notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                    VALUES('{region}', '{in_comm}', '{tech}', {vint}, '{out_comm}', {eff},
                    '(PJ/PJ) from {eff_metric} for {aeo_class}', '{ref.id}', 1, 2, 2, 2, 3, '{utils.data_id(region)}')"""
                )
    

    ##############################################################
    # NRCan data (existing)
    ##############################################################

    for tech, row in nrcan_techs.iterrows():
        
        ## CostFixed from aeo equivalent tech
        aeo_class = row['aeo_class']
        if pd.isna(aeo_class): continue # should only apply to appliances other

        equiv_tech = aeo_techs.loc[aeo_techs['aeo_class']==aeo_class].index.values[0]
        note = f"Assumed same as {equiv_tech}."


        ## Lifetime
        # Doing this by region so that some regions can be skipped at aggregation phase
        lifetime = config.lifetimes[aeo_class]

        curs.execute(
            f"""REPLACE INTO
            LifetimeTech(region, tech, lifetime,
            notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
            VALUES('{region}', '{tech}', {lifetime},
            '{note}', '{ref.id}', 1, 2, 2, 3, 3, '{utils.data_id(region)}')"""
        )
        

        ## CostFixed
        cost_fixed = aeo_techs.loc[equiv_tech, 'cost_fixed'] * config.params['conversion_factors']['cost']['fixed']
        cost_fixed = conv_curr(cost_fixed)
        if cost_fixed == 0: continue

        for vint in config.tech_vints[tech]:
            for period in config.model_periods:
                
                if period < vint or vint + lifetime <= period: continue

                curs.execute(
                    f"""REPLACE INTO
                    CostFixed(region, period, tech, vintage, cost, units,
                    notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                    VALUES('{region}', {period}, '{tech}', {vint}, {cost_fixed}, '(M$/{cap_unit}.y)',
                    '{note}', '{ref_updated.id}', 1, 2, 2, 3, 3, '{utils.data_id(region)}')"""
                )
    
    """
    ##############################################################
        Capacity to Activity
    ##############################################################
    """

    ## Adding arbitrary c2a as a default value to all technologies
    note = ("Arbitrary but sufficiently high to satisfy demand in all hours. Actual activity cont"
            "rolled by AnnualCapacityFactor tables and DemandActivity constraint. Result is that all technologi"
            "es are utilised in consistent proportions throughout the year, according to relative size of annua"
            "l capacity factors.")

    ## NRCan existing stock
    for tech, row in nrcan_techs.iterrows():
        end_use = row['end_use']

        c2a = end_use_demands.loc[end_use, 'c2a']
        if pd.isna(c2a): continue

        unit = f"{end_use_demands.loc[end_use, 'dem_unit']}/{end_use_demands.loc[end_use, 'cap_unit']}.y" # ACT/CAP.y
        curs.execute(
            f"""REPLACE INTO
            CapacityToActivity(region, tech, c2a, notes, data_id)
            VALUES('{region}', '{tech}', {c2a}, '({unit}) {note}', '{utils.data_id(region)}')"""
        )

    ## AEO future stock
    for tech, row in aeo_techs.iterrows():

        if not row['include_new']: continue

        end_uses = row['end_uses'].split('+')

        c2a = end_use_demands.loc[end_uses[0], 'c2a'] # Must be the same for all end uses anyway
        unit = f"{end_use_demands.loc[end_uses[0], 'dem_unit']}/{end_use_demands.loc[end_uses[0], 'cap_unit']}.y" # ACT/CAP.y
        curs.execute(
            f"""REPLACE INTO
            CapacityToActivity(region, tech, c2a, notes, data_id)
            VALUES('{region}', '{tech}', {c2a}, '({unit}) {note}', '{utils.data_id(region)}')"""
        )
    

    conn.commit()
    conn.close()



# For non-regional post-subsector aggregation
def post_process():

    for region in config.model_regions: post_process_region(region)

    # Connect to the new database file
    conn = sqlite3.connect(config.database_file)
    curs = conn.cursor() # Cursor object interacts with the sqlite db
    

    """
    ##############################################################
        Existing time periods
    ##############################################################
    """

    # Add all existing vintages to existing time periods
    vints = set([fetch[0] for fetch in curs.execute(f"SELECT vintage FROM Efficiency").fetchall() if fetch[0] not in config.model_periods])

    for vint in vints:
        curs.execute(
            f"""INSERT OR IGNORE INTO
            TimePeriod(period, flag)
            VALUES({vint}, 'e')"""
        )


    """
    ##############################################################
        References
    ##############################################################
    """

    # Add all references in the bibliography to the references tables
    for reference in config.refs:
        curs.execute(
            f"""REPLACE INTO
            DataSource(source_id, source, data_id)
            VALUES('{reference.id}', '{reference.citation}', "{utils.data_id()}")"""
        )
        

    """
    ##############################################################
        Data IDs
    ##############################################################
    """

    for id in sorted(config.data_ids):
        curs.execute(
            f"""REPLACE INTO
            DataSet(data_id)
            VALUES('{id}')"""
        )

    # Check for missing data IDs
    print("Checking that all data has a dataset ID...", end="")
    tables = [t[0] for t in curs.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]

    all_good = True
    for table in tables:
        cols = [c[1] for c in curs.execute(f"PRAGMA table_info({table})").fetchall()]
        if "data_id" in cols:
            bad_rows = pd.read_sql_query(f"SELECT * FROM {table} WHERE data_id is NULL", conn)
            if len(bad_rows) > 0:
                print(f"\nFound some rows missing data IDs in {table}")
                print(bad_rows)
                all_good = False

    if all_good: print(" All good!")
        

    conn.commit()
    conn.close()

    print(f"Post-aggregation complete.\n")



# For regional post-subsector aggregation
def post_process_region(region):

    # Connect to the new database file
    conn = sqlite3.connect(config.database_file)
    curs = conn.cursor() # Cursor object interacts with the sqlite db

    """
    ##############################################################
        Annual Capacity Factor
    ##############################################################
    """

    ref = config.refs.get('nrcan_statcan')

    # In case we need to remove something
    all_tables = [fetch[0] for fetch in curs.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
    t_tables = [table for table in all_tables if 'tech' in [description[0] for description in curs.execute(f"SELECT * FROM '{table}'").description]]
    rt_tables = [table for table in t_tables if 'region' in [description[0] for description in curs.execute(f"SELECT * FROM '{table}'").description]]
        
    ## AEO future stock
    # Copy from NRCan existing stock    
    for tech, row in aeo_techs.iterrows():

        if not row['include_new']: continue

        if pd.isna(row['nrcan_equiv']):
            print(f"{tech} has no specific NRCan equivalent and so will have no annual capacity factor.")
            continue # no NRCan equivalent given
        
        end_uses = row['end_uses'].split('+')
        nrcan_equivs = row['nrcan_equiv'].split('+')
        nrcan_equivs = [nrcan_techs.loc[nrcan_techs['end_use'] + " - " + nrcan_techs['description'] == nrcan_equiv].index.values[0] for nrcan_equiv in nrcan_equivs]

        for e in range(len(end_uses)):
            
            end_use = end_uses[e]
            nrcan_tech = nrcan_equivs[e]
            
            out_comm = end_use_demands.loc[end_use, 'comm']
            
            note = f"Assumed same as {nrcan_equivs[e]}"
            
            # Get annual capacity factor from equivalent nrcan tech for which we have data
            acf = curs.execute(
                f"""SELECT factor FROM LimitAnnualCapacityFactor
                WHERE tech == '{nrcan_tech}'
                AND region == '{region}'
                AND output_comm == '{out_comm}'
                AND operator == 'le'"""
            ).fetchone()
            if not acf:
                print(
                    f"Could not get an equivalent ACF for ({region}, {tech}). Looked for ({region}, {nrcan_tech}). "
                    "Removing this region-tech pair from the model."
                )
                for table in rt_tables:
                    curs.execute(f"DELETE FROM '{table}' WHERE tech == '{tech}' AND region == '{region}'")
                continue
            else: acf = acf[0]

            for vintage in config.model_periods:
                curs.execute(
                    f"""REPLACE INTO
                    LimitAnnualCapacityFactor(region, tech, vintage, output_comm, operator, factor,
                    notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                    VALUES('{region}', '{tech}', {vintage}, '{out_comm}', 'ge', {acf*0.95},
                    '{note}', '{ref.id}', 1, 1, 3, 3, 3, '{utils.data_id(region)}')"""
                )
                curs.execute(
                    f"""REPLACE INTO
                    LimitAnnualCapacityFactor(region, tech, vintage, output_comm, operator, factor,
                    notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                    VALUES('{region}', '{tech}', {vintage}, '{out_comm}', 'le', {acf},
                    '{note}', '{ref.id}', 1, 1, 3, 3, 3, '{utils.data_id(region)}')"""
                )
         


    conn.commit()
    conn.close()



# Doing all regions at once because some regions might share equivalent states and this process is slow
def aggregate_dsd():

    print("Aggregating DSDs...")

    conn = sqlite3.connect(config.database_file)
    curs = conn.cursor() # Cursor object interacts with the sqlite db

    """
    ##############################################################
        Demand specific distribution
    ##############################################################
    """

    weather_year = config.params['weather_year']
    reference = (f"{config.params['resstock']['reference']}; "
                 f"{config.params['weather']['reference']}; "
                 f"{config.params['nrcan_reference']}; ")
    ref = config.refs.add('dsd', reference)

    res_config = pd.read_csv(config.input_files + 'resstock.csv', index_col=0)
    cons = dict() # 8760 hourly energy consumption by state, housing type, and end use, (kWh)

    ## Get end use energy consumptions from resstock columns and divide by number of housing units represented
    for state in config.regions.loc[config.regions['include']]['us_state'].unique():

        cons[state] = dict()

        for housing_type, file_name in config.params['resstock']['housing_files'].items():
            cons[state][housing_type] = dict()

            df_res = utils.get_data(config.params['resstock']['url'].replace("<s>",state.upper()).replace("<f>", file_name).replace("<s>", state.lower()))
            df_res = df_res.fillna(0).set_index('timestamp')
            stock = df_res['units_represented'].iloc[0]

            for end_use in config.end_use_demands.index:
                
                res_cols = res_config.loc[res_config['end_use'] == end_use]

                for res_col in res_cols.index:
                    
                    # Divide consumption by number of units represented to get consumption per household
                    con = df_res[res_col].iloc[[35039,*range(3,4*8760-3,4)]].astype(float).clip(lower=0) / float(stock) # 15-minutely so take every 4th

                    if end_use in cons[state][housing_type].keys(): cons[state][housing_type][end_use] += con
                    else: cons[state][housing_type][end_use] = con

    
    ## Multiply energy consumptions from resstock by province housing stocks, apply weather mapping, then normalise to DSD
    for region in config.model_regions:

        data_id = utils.data_id(region)

        print(f"Aggregating DSDs for {region}...")

        row = config.regions.loc[region]
        state = row['us_state']

        note = (f"ResStock data for {state} (NREL, 2021) disaggregated by end use and building archetype and mapped from {state} {weather_year} air temperature "
                f"and humidity to {region} {weather_year} temperature and humidity, taking the mean of matched hours (Renewables Ninja, {weather_year}). "
                f"Reaggregated for {base_year} existing stock of housing archetypes in {region} (NRCan, {base_year})"
                f"Chronological linear interpolation for any missing data.")

        # Table 14: Total Households by Building Type and Energy Source
        t14 = utils.get_compr_db(region, 14, 9, 12)[base_year] / 100 # % shares

        # Create figure and axes
        fig, axs = pp.subplots(4, 3, figsize=(15, 10))  # 4 rows, 3 columns
        axs[-1, -1].axis('off')
        fig.tight_layout()
        fig.subplots_adjust(wspace=0.2, hspace=0.3, top=0.9, left=0.05, right=0.95, bottom=0.05)
        fig.suptitle(f"{region} demand specific distributions (blue). Weekly profile in red.")

        p = 0 # plot tracker
        for end_use, eud_config in config.end_use_demands.iterrows():

            demand_comm = eud_config['comm']

            # Consumption for each housing type times provincial stock of that housing type
            con_us = sum([t14[housing_type] * cons[state][housing_type][end_use] for housing_type in t14.index])
            con_us = utils.realign_timezone(con_us, from_timezone='EST')

            # Map space heating, cooling to temperature and dew point temp (humidity). Note: this might introduce weather efficiency to the demand!
            if eud_config['use_weather_map']: con_ca, time_of_week = weather_mapping.map_data(region, con_us.to_numpy())
            else: con_ca = con_us

            # Apply tolerance and normalise
            con_ca.loc[con_ca < con_ca.mean() * config.params['dsd_tolerance']] = 0
            dsd = (con_ca / con_ca.sum()).to_list()

            # For plotting DSDs
            row = p // 3 # integer division to determine row
            col = p % 3 # modulo to determine column
            axs[row, col].plot(dsd)
            #if eud_config['use_weather_map']: axs[row, col].twinx().plot(range(0,8736,52), time_of_week, 'r-') # time of week variation overlaid
            axs[row, col].set_title(end_use)
            p+=1

            data = []
            for period in config.model_periods:
                for h, time in config.time.iterrows():

                    seas = time['season']
                    tod = time['tod']

                    if tod == config.time['tod'].iloc[0]:
                        data.append([
                            region, period, seas, tod, demand_comm, dsd[h],
                            note, ref.id,
                            1, 3, 2, 1, 3,
                            data_id,
                        ])
                    else:
                        data.append([
                            region, period, seas, tod, demand_comm, dsd[h],
                            None, None,
                            None, None, None, None, None,
                            data_id,
                        ])

            curs.executemany(
                "REPLACE INTO "
                "DemandSpecificDistribution(region, period, season, tod, demand_name, dsd, "
                "notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                data
            )

        pp.tight_layout()


    conn.commit()
    conn.close()

    print(f"Demand specific distribution data aggregated into {os.path.basename(config.database_file)}\n")



def aggregate_emissions():

    # Connect to the new database file
    conn = sqlite3.connect(config.database_file)
    curs = conn.cursor() # Cursor object interacts with the sqlite db
    

    """
    ##############################################################
        Emission Activity
    ##############################################################
    """

    emis_comm = config.params['emission_commodity']
    emis_units = config.params['emission_activity_units']

    ref = config.refs.add('epa', config.params['epa_reference'])

    # Get emissions factors for fuels in ktCO2eq/PJ_in
    emis_fact = utils.get_data('https://www.epa.gov/system/files/other-files/2025-01/ghg-emission-factors-hub-2025.xlsx', skiprows=13, nrows=76, index_col=2)
    emis_fact = emis_fact[['CO2 Factor', 'CH4 Factor', 'N2O Factor']].iloc[1::].dropna()
    emis_fact = emis_fact[pd.to_numeric(emis_fact['CO2 Factor'], errors='coerce').notnull()] # Removing NaN rows
    for fact in emis_fact.columns: emis_fact[fact] = emis_fact[fact].astype(float) * conversion_factors['epa_units'][fact.strip(' Factor')] * conversion_factors['gwp'][fact.strip(' Factor')]
    emis_fact[emis_comm] = emis_fact.sum(axis=1)

    for tech in config.all_techs:

        # Valid vintages and efficiencies from Efficiency table
        rows = curs.execute(f"SELECT region, input_comm, tech, vintage, output_comm, efficiency FROM Efficiency WHERE tech == '{tech}'").fetchall()

        for row in rows:

            # Input fuel by epa naming convention
            epa_fuel = config.fuel_commodities.loc[config.fuel_commodities['comm'] == row[1], 'epa_fuel'].iloc[0]
            if pd.isna(epa_fuel): continue # doesn't need emissions

            # EmissionActivity is tied to OUTPUT energy so divide by efficiency
            emis_act = emis_fact.loc[epa_fuel, emis_comm] / row[5]

            # Note assumed fuel
            note = f"Emissions factor using {epa_fuel} (EPA, {config.params['epa_year']}) divided by efficiency as emissions are per output unit energy."

            curs.execute(
                f"""REPLACE INTO
                EmissionActivity(region, emis_comm, input_comm, tech, vintage, output_comm, activity, units,
                notes, data_source, dq_cred, dq_geog, dq_struc, dq_tech, dq_time, data_id)
                VALUES('{row[0]}', '{emis_comm}', '{row[1]}', '{row[2]}', {row[3]}, '{row[4]}', {emis_act}, '{emis_units}',
                '{note}', '{ref.id}', 1, 3, 2, 4, 1, '{utils.data_id(row[0])}')"""
            )
    

    conn.commit()
    conn.close()

    print(f"Emissions data aggregated into {os.path.basename(config.database_file)}\n")



def aggregate_imports():

    conn = sqlite3.connect(config.database_file)
    curs = conn.cursor()

    # Get which fuel commodities are actually being used
    used_comms = set([c[0] for c in curs.execute(f"SELECT input_comm FROM Efficiency").fetchall()])

    # Get the last period the import is used in this region and convert to a lifetime, to prevent supply orphans
    df_life = pd.read_sql_query("SELECT region, tech, lifetime FROM LifetimeTech", conn).set_index(['region','tech']).astype(int)
    df_eff = pd.read_sql_query("SELECT region, input_comm, tech, vintage FROM Efficiency", conn)
    df_eff = df_eff.loc[df_eff['tech'].isin(df_life.index.get_level_values('tech'))]
    df_eff['life'] = [
        row['vintage'] + df_life.loc[(row['region'], row['tech'])].iloc[0] - config.model_periods[0]
        for (_, row) in df_eff.iterrows()
    ]
    df_life = df_eff.groupby(['region','input_comm'])['life'].max().astype(int)

    for tech, row in config.import_techs.iterrows():
        
        # Get CANOE nomenclature for imported commodity
        out_comm = config.fuel_commodities.loc[row['out_comm']]

        # Make sure the model is using this imported commodity otherwise skip
        if out_comm['comm'] not in used_comms: continue
        
        description = f"import dummy for {out_comm['description']}"

        curs.execute(
            f"""REPLACE INTO
            Technology(tech, flag, sector, description, data_id)
            VALUES('{tech}', 'p', 'residential', '{description}', '{utils.data_id()}')"""
        )

        for flag in row['flags'].split(','):
            curs.execute(
                f"""UPDATE Technology
                SET {flag} == 1
                WHERE tech == '{tech}'"""
            )
        
        for region in config.model_regions:

            if (region, out_comm['comm']) not in df_life.index: continue
            
            # The dummy needs to retire when it is no longer used
            # (or it will be orphaned and removed by network checks)
            life = df_life.loc[(region, out_comm['comm'])]

            curs.execute(
                f"""REPLACE INTO
                Efficiency(region, input_comm, tech, vintage, output_comm, efficiency, notes, data_id)
                VALUES('{region}', '{config.fuel_commodities.loc[row['in_comm'], 'comm']}', '{tech}',
                '{config.model_periods[0]}', '{out_comm['comm']}', 1, '{description})', '{utils.data_id(region)}')"""
            )
            
            if life < config.model_periods[-1] - config.model_periods[0]:
                curs.execute(
                    f"""REPLACE INTO
                    LifetimeTech(region, tech, lifetime, notes, data_id)
                    VALUES("{region}", "{tech}", "{life}", "(y) retires when no longer used", "{utils.data_id(region)}")"""
                )
            
    conn.commit()
    conn.close()

    print(f"Imports aggregated into {os.path.basename(config.database_file)}\n")



def cleanup():

    # Connect to the new database file
    conn = sqlite3.connect(config.database_file)
    curs = conn.cursor() # Cursor object interacts with the sqlite db


    """
    ##############################################################
        Existing tech with no capacity
    ##############################################################
    """

    # Get all tables with tech and region indices
    all_tables = [fetch[0] for fetch in curs.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
    t_tables = [table for table in all_tables if 'tech' in [description[0] for description in curs.execute(f"SELECT * FROM '{table}'").description]]
    rt_tables = [table for table in t_tables if 'region' in [description[0] for description in curs.execute(f"SELECT * FROM '{table}'").description]]

    for region in config.model_regions:
        for tech, row in config.existing_techs.iterrows():
            if row['end_use'] == 'appliances other': continue # Does not have capacity

            exs_cap = curs.execute(f"SELECT sum(capacity) FROM ExistingCapacity WHERE tech == '{tech}' and region == '{region}'").fetchone()[0]
            if not exs_cap or exs_cap < config.params['existing_cap_tolerance']:
                
                # If no existing capacity for an existing tech, purge tech/region combo from database
                for table in rt_tables: 
                    curs.execute(f"DELETE FROM '{table}' WHERE tech == '{tech}' AND region == '{region}'")

                print(f"Cleaned up existing region-tech with little or no existing capacity: ({region}, {tech})")

    for tech, row in config.existing_techs.iterrows():
        if row['end_use'] == 'appliances other': continue # Does not have capacity

        exs_cap = curs.execute(f"SELECT sum(capacity) FROM ExistingCapacity WHERE tech == '{tech}'").fetchone()[0]
        if not exs_cap or exs_cap == 0:
            
            # If no existing capacity for an existing tech, purge tech/region combo from database
            for table in t_tables: 
                curs.execute(f"DELETE FROM '{table}' WHERE tech == '{tech}'")

            print(f"Cleaned up existing tech with no existing capacity: {tech}")

    conn.commit()
    conn.close()

    print(f"Cleanup complete.\n")



if __name__ == "__main__":
    
    aggregate()