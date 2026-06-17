"""
Aggregates residential non-subsector-specific data
Written by Ian David Elder for the CANOE model
"""

import canoe_residential.utils as utils
import canoe_residential.nrcan as nrcan
import pandas as pd
from scipy.special import gamma
import sqlite3
import os
from canoe_schema.v4_0 import models as schema_models
import canoe_residential.space_heating as space_heating
import canoe_residential.space_cooling as space_cooling
import canoe_residential.water_heating as water_heating
import canoe_residential.lighting as lighting
import canoe_residential.appliances as appliances
from matplotlib import pyplot as pp
import canoe_residential.weather_mapping as weather_mapping
from canoe_residential.currency_conversion import conv_curr
from canoe_residential.common import ResidentialRuntime



def aggregate(runtime: ResidentialRuntime, conn: sqlite3.Connection):

    print("Aggregating sub-sector data...\n")

    pre_process(runtime, conn)

    ## Aggregate subsectors
    space_heating.aggregate(runtime, conn)
    space_cooling.aggregate(runtime, conn)
    water_heating.aggregate(runtime, conn)
    lighting.aggregate(runtime, conn)
    appliances.aggregate(runtime, conn)

    if runtime.cfg.include_dsd: aggregate_dsd(runtime, conn)
    if runtime.cfg.include_emissions: aggregate_emissions(runtime, conn)
    # if config.params['include_imports']: aggregate_imports() # no longer supported

    post_process(runtime, conn)

    cleanup(runtime, conn)

    print(f"Sub-sector data aggregated into {os.path.basename(runtime.cfg.db_dir)}\n")



# For non-regional aggregation
def pre_process(runtime: ResidentialRuntime, conn: sqlite3.Connection):

    curs = conn.cursor() # Cursor object interacts with the sqlite db


    """
    ##############################################################
        Basic parameters
    ##############################################################
    """

    # Time-slice tables: idempotent INSERT OR IGNORE so re-runs are safe.
    # canoe-base is expected to seed these; canoe-residential inserts them if absent.
    # v4.0: SeasonLabel and TimeSegmentFraction are gone; segment_fraction is now a
    # field on TimeSeason (period-independent). TimeOfDay.hours defaults to 1.0 (1 h/tod).
    tods_per_season = runtime.time.groupby('season')['tod'].count()
    total_hours = len(runtime.time)

    for i, tod in enumerate(runtime.time['tod'].unique()):
        sql, params = schema_models.TimeOfDay(sequence=i, tod=tod).to_insert_or_ignore_sql()
        curs.execute(sql, params)

    for i, season in enumerate(runtime.time['season'].unique()):
        seg_frac = tods_per_season[season] / total_hours
        sql, params = schema_models.TimeSeason(
            sequence=i,
            season=season,
            segment_fraction=seg_frac,
        ).to_insert_or_ignore_sql()
        curs.execute(sql, params)

    # TimePeriod (future) and Region are B-category (Global) tables owned by canoe-base.
    # canoe-residential validates them in Step 0 (residential_sector.build_database)
    # and never writes to them.


    """
    ##############################################################
        Commodities
    ##############################################################
    """

    for _fuel, row in runtime.fuel_commodities.iterrows():
        sql, params = schema_models.Commodity(
            name=row['comm'],
            flag=row['flag'],
            description=f"(PJ) {row['description']}",
            data_id=utils.data_id(runtime),
        ).to_insert_or_ignore_sql()
        curs.execute(sql, params)
    for _end_use, row in runtime.end_use_demands.iterrows():
        sql, params = schema_models.Commodity(
            name=row['comm'],
            flag='d',
            description=f"(PJ) {row['description']}",
            data_id=utils.data_id(runtime),
        ).to_insert_or_ignore_sql()
        curs.execute(sql, params)

    if runtime.cfg.include_emissions:
        # CO2-equivalent emission commodity: INSERT OR IGNORE so canoe-base or another
        # module can define it first without conflict (see DECISIONS.md).
        sql, params = schema_models.Commodity(
            name=runtime.cfg.emission_commodity,
            flag='e',
            description='(ktCO2eq) CO2-equivalent emissions',
            data_id=utils.data_id(runtime),
        ).to_insert_or_ignore_sql()
        curs.execute(sql, params)



    """
    ##############################################################
        Technologies, lifetimes, tech-vintage pairs
    ##############################################################
    """

    ##############################################################
    # Lifetimes
    ##############################################################

    for aeo_class in runtime.aeo_res_class.index:

        # Get lifetime from mean of weibull distribution
        weibull_k = runtime.aeo_res_class.loc[aeo_class, 'Weibull K']
        weibull_l = runtime.aeo_res_class.loc[aeo_class, 'Weibull λ']

        # There are double ups of class for heat pumps because two end uses
        if type(weibull_k) is pd.Series: weibull_k = weibull_k.iloc[0]
        if type(weibull_l) is pd.Series: weibull_l = weibull_l.iloc[0]

        # Calculate lifetime and add to lifetimes dictionary
        lifetime = round(weibull_l * gamma(1 + 1/weibull_k)) # mean of weibull distribution
        runtime.lifetimes[aeo_class] = lifetime


    ##############################################################
    # AEO data (new)
    ##############################################################

    for tech, row in runtime.new_techs.iterrows():

        if not row['include_new']: continue

        tech_desc = f"{row.loc['end_uses']} - {row.loc['description']}"
        tech_kwargs = {
            'tech': tech,
            'flag': 'p',
            'sector': 'residential',
            'description': tech_desc,
            'data_id': utils.data_id(runtime),
        }
        for flag in row['flags'].split(','):
            tech_kwargs[flag.strip()] = 1
        sql, params = schema_models.Technology(**tech_kwargs).to_insert_or_ignore_sql()
        curs.execute(sql, params)

        # Add future vintages to vintage dictionary
        runtime.tech_vints[tech] = runtime.cfg.future_periods


    ##############################################################
    # NRCan data (existing)
    ##############################################################

    for tech, row in runtime.existing_techs.iterrows():

        tech_desc = f"{row.loc['end_use']} - {row.loc['description']}"

        tech_kwargs = {
            'tech': tech,
            'flag': 'p',
            'sector': 'residential',
            'description': tech_desc,
            'data_id': utils.data_id(runtime),
        }
        for flag in row['flags'].split(','):
            tech_kwargs[flag.strip()] = 1
        sql, params = schema_models.Technology(**tech_kwargs).to_insert_or_ignore_sql()
        curs.execute(sql, params)

        # Get equivalent future tech
        aeo_class = row.loc['aeo_class']
        if pd.isna(aeo_class): continue # should only apply to appliances other

        # Add lifetimes and feasible vintages to config dictionaries
        exs_vints, _weights = utils.stock_vintages(runtime.lifetimes[aeo_class], runtime.cfg.period_step, runtime.cfg.future_periods[0])
        runtime.tech_vints[tech] = exs_vints


    for region in runtime.cfg.province_list: pre_aggregate_region(region, runtime, conn)

    print(f"Pre aggregation complete.\n")



# For region-specific aggregation
def pre_aggregate_region(region: str, runtime: ResidentialRuntime, conn: sqlite3.Connection):

    curs = conn.cursor() # Cursor object interacts with the sqlite db

    """
    ##############################################################
        Efficiency, CostInvest, CostFixed
    ##############################################################
    """

    cens_div = runtime.regions.loc[region, 'us_census_div']

    ref = runtime.refs.get('aeo')
    ref_updated = runtime.refs.add('aeo_updated', runtime.cfg.aeo_updated_reference)

    # Narrow down the dataframe with each nested section to speed things up
    df0 = runtime.aeo_res_equip.loc[(runtime.aeo_res_equip['Census Division'] == cens_div) | (runtime.aeo_res_equip['Census Division'] == 11)]


    ##############################################################
    # AEO data (new)
    ##############################################################

    # All technologies from aeo technologies input csv
    for tech, row in runtime.new_techs.iterrows():

        if not row['include_new']: continue

        aeo_class = row['aeo_class']
        aeo_equip = row['aeo_equip']

        in_comm = runtime.fuel_commodities.loc[row['fuel'], 'comm']
        lifetime = runtime.lifetimes[aeo_class]

        ## LifetimeTech
        note = f'(y) Mean of Weibull distribution for {aeo_class}'
        ref = runtime.refs.get('aeo')
        sql, params = schema_models.LifetimeTech(
            region=region,
            tech=tech,
            lifetime=lifetime,
            notes=note,
            data_source=ref.id,
            dq_cred=1,
            dq_geog=2,
            dq_struc=2,
            dq_tech=2,
            dq_time=3,
            data_id=utils.data_id(runtime, region),
        ).to_insert_or_ignore_sql()
        curs.execute(sql, params)

        if type(df0) is pd.DataFrame: df1 = df0.loc[aeo_equip]
        elif type(df0) is pd.Series: df1 = df0 # only one row remaining

        end_uses = row.loc['end_uses'].split("+")
        end_use_ids = row.loc['end_use_ids'].split("+")

        cap_unit = runtime.end_use_demands.loc[end_uses[0], 'cap_unit']

        # All future periods are valid vintages
        for vint in runtime.tech_vints[tech]:

            yr = utils.data_year(vint, runtime) # end-of-period data year for this vintage

            if type(df1) is pd.DataFrame:
                df2 = df1.loc[(df1['First Year']<=yr) & (yr<=df1['Last Year'])]
                cost_invest = df2.loc[df2['Replacement Cost'] != 0]['Replacement Cost'].iloc[0]
            elif type(df1) is pd.Series:
                df2 = df1 # only one row remaining
                cost_invest = df2['Replacement Cost']

            # AEO table splits heat pump costs between heating and cooling, annoyingly
            if row.loc['end_uses'] == 'space heating+space cooling': cost_invest *= 2


            ## CostInvest
            cost_invest *= runtime.cfg.conversion_factors.cost.invest
            cost_invest = conv_curr(runtime, cost_invest)

            sql, params = schema_models.CostInvest(
                region=region,
                tech=tech,
                vintage=vint,
                cost=cost_invest,
                units=f"(M$/{cap_unit})",
                notes=f"{yr} replacement cost for {aeo_equip}",
                data_source=ref.id,
                dq_cred=1,
                dq_geog=2,
                dq_struc=2,
                dq_tech=2,
                dq_time=3,
                data_id=utils.data_id(runtime, region),
            ).to_insert_or_ignore_sql()
            curs.execute(sql, params)


            ## CostFixed
            cost_fixed = row['cost_fixed'] * runtime.cfg.conversion_factors.cost.fixed
            cost_fixed = conv_curr(runtime, cost_fixed)

            if cost_fixed != 0:
                for period in runtime.cfg.future_periods:

                    if period < vint or vint + lifetime <= period: continue

                    sql, params = schema_models.CostFixed(
                        region=region,
                        period=period,
                        tech=tech,
                        vintage=vint,
                        cost=cost_fixed,
                        units=f"(M$/{cap_unit}.y)",
                        notes=f"{yr} Fixed O&M cost for {aeo_equip}",
                        data_source=ref_updated.id,
                        dq_cred=1,
                        dq_geog=2,
                        dq_struc=2,
                        dq_tech=2,
                        dq_time=3,
                        data_id=utils.data_id(runtime, region),
                    ).to_insert_or_ignore_sql()
                    curs.execute(sql, params)


            # For each end use the technology supplies (heat pumps do heating and cooling)
            for e in range(len(end_uses)):

                end_use = end_uses[e]
                end_use_id = int(end_use_ids[e])

                out_comm = runtime.end_use_demands.loc[end_use, 'comm']

                if type(df2) is pd.DataFrame: eff = df2.loc[(df2['End Use'] == int(end_use_id))]['Efficiency'].iloc[0]
                elif type(df2) is pd.Series: eff = df2['Efficiency'] # only one row remaining

                eff_metric = runtime.aeo_res_class.loc[runtime.aeo_res_class['End Use'] == int(end_use_id)].loc[aeo_class, 'Efficiency Metric']
                if type(eff_metric) is pd.Series: eff_metric = eff_metric.iloc[0]
                if eff_metric in runtime.cfg.conversion_factors.efficiency.keys(): eff *= runtime.cfg.conversion_factors.efficiency[eff_metric]

                ## Default Efficiency
                sql, params = schema_models.Efficiency(
                    region=region,
                    input_comm=in_comm,
                    tech=tech,
                    vintage=vint,
                    output_comm=out_comm,
                    efficiency=eff,
                    notes=f"(PJ/PJ) from {eff_metric} for {aeo_class}",
                    data_source=ref.id,
                    dq_cred=1,
                    dq_geog=2,
                    dq_struc=2,
                    dq_tech=2,
                    dq_time=3,
                    data_id=utils.data_id(runtime, region),
                ).to_insert_or_ignore_sql()
                curs.execute(sql, params)


    ##############################################################
    # NRCan data (existing)
    ##############################################################

    for tech, row in runtime.existing_techs.iterrows():

        ## CostFixed from aeo equivalent tech
        aeo_class = row['aeo_class']
        if pd.isna(aeo_class): continue # should only apply to appliances other

        equiv_tech = runtime.new_techs.loc[runtime.new_techs['aeo_class']==aeo_class].index.values[0]
        note = f"Assumed same as {equiv_tech}."


        ## Lifetime
        # Doing this by region so that some regions can be skipped at aggregation phase
        lifetime = runtime.lifetimes[aeo_class]

        sql, params = schema_models.LifetimeTech(
            region=region,
            tech=tech,
            lifetime=lifetime,
            notes=note,
            data_source=ref.id,
            dq_cred=1,
            dq_geog=2,
            dq_struc=2,
            dq_tech=3,
            dq_time=3,
            data_id=utils.data_id(runtime, region),
        ).to_insert_or_ignore_sql()
        curs.execute(sql, params)


        ## CostFixed
        cost_fixed = runtime.new_techs.loc[equiv_tech, 'cost_fixed'] * runtime.cfg.conversion_factors.cost.fixed
        cost_fixed = conv_curr(runtime, cost_fixed)
        if cost_fixed == 0: continue

        for vint in runtime.tech_vints[tech]:
            for period in runtime.cfg.future_periods:

                if period < vint or vint + lifetime <= period: continue

                sql, params = schema_models.CostFixed(
                    region=region,
                    period=period,
                    tech=tech,
                    vintage=vint,
                    cost=cost_fixed,
                    units=f"(M$/{cap_unit}.y)",
                    notes=note,
                    data_source=ref_updated.id,
                    dq_cred=1,
                    dq_geog=2,
                    dq_struc=2,
                    dq_tech=3,
                    dq_time=3,
                    data_id=utils.data_id(runtime, region),
                ).to_insert_or_ignore_sql()
                curs.execute(sql, params)

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
    for tech, row in runtime.existing_techs.iterrows():
        end_use = row['end_use']

        c2a = runtime.end_use_demands.loc[end_use, 'c2a']
        if pd.isna(c2a): continue

        unit = f"{runtime.end_use_demands.loc[end_use, 'dem_unit']}/{runtime.end_use_demands.loc[end_use, 'cap_unit']}.y" # ACT/CAP.y
        sql, params = schema_models.CapacityToActivity(
            region=region,
            tech=tech,
            c2a=c2a,
            notes=f"({unit}) {note}",
            data_id=utils.data_id(runtime, region),
        ).to_insert_or_ignore_sql()
        curs.execute(sql, params)

    ## AEO future stock
    for tech, row in runtime.new_techs.iterrows():

        if not row['include_new']: continue

        end_uses = row['end_uses'].split('+')

        c2a = runtime.end_use_demands.loc[end_uses[0], 'c2a'] # Must be the same for all end uses anyway
        unit = f"{runtime.end_use_demands.loc[end_uses[0], 'dem_unit']}/{runtime.end_use_demands.loc[end_uses[0], 'cap_unit']}.y" # ACT/CAP.y
        sql, params = schema_models.CapacityToActivity(
            region=region,
            tech=tech,
            c2a=c2a,
            notes=f"({unit}) {note}",
            data_id=utils.data_id(runtime, region),
        ).to_insert_or_ignore_sql()
        curs.execute(sql, params)



# For non-regional post-subsector aggregation
def post_process(runtime: ResidentialRuntime, conn: sqlite3.Connection):

    for region in runtime.cfg.province_list: post_process_region(region, runtime, conn)

    curs = conn.cursor() # Cursor object interacts with the sqlite db


    """
    ##############################################################
        Existing time periods
    ##############################################################
    """

    # Add all existing vintages to existing time periods
    vints = set([fetch[0] for fetch in curs.execute(f"SELECT vintage FROM {schema_models.Efficiency.__table_name__}").fetchall() if fetch[0] not in runtime.cfg.future_periods])

    for vint in vints:
        sql, params = schema_models.TimePeriod(
            period=vint,
            flag='e',
        ).to_insert_or_ignore_sql()
        curs.execute(sql, params)


    """
    ##############################################################
        References
    ##############################################################
    """

    # Add all references in the bibliography to the references tables
    for reference in runtime.refs:
        sql, params = schema_models.DataSource(
            source_id=reference.id,
            source=reference.citation,
            data_id=utils.data_id(runtime),
        ).to_insert_or_ignore_sql()
        curs.execute(sql, params)


    """
    ##############################################################
        Data IDs
    ##############################################################
    """

    for id in sorted(runtime.data_ids):
        sql, params = schema_models.DataSet(data_id=id).to_insert_or_ignore_sql()
        curs.execute(sql, params)

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

    print(f"Post-aggregation complete.\n")



# For regional post-subsector aggregation
def post_process_region(region: str, runtime: ResidentialRuntime, conn: sqlite3.Connection):

    curs = conn.cursor() # Cursor object interacts with the sqlite db

    """
    ##############################################################
        Annual Capacity Factor
    ##############################################################
    """

    ref = runtime.refs.get('nrcan_statcan')

    # In case we need to remove something
    all_tables = [fetch[0] for fetch in curs.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
    t_tables = [table for table in all_tables if 'tech' in [description[0] for description in curs.execute(f"SELECT * FROM '{table}'").description]]
    rt_tables = [table for table in t_tables if 'region' in [description[0] for description in curs.execute(f"SELECT * FROM '{table}'").description]]

    ## AEO future stock
    # Copy from NRCan existing stock
    for tech, row in runtime.new_techs.iterrows():

        if not row['include_new']: continue

        if pd.isna(row['nrcan_equiv']):
            print(f"{tech} has no specific NRCan equivalent and so will have no annual capacity factor.")
            continue # no NRCan equivalent given

        end_uses = row['end_uses'].split('+')
        nrcan_equivs = row['nrcan_equiv'].split('+')
        nrcan_equivs = [runtime.existing_techs.loc[runtime.existing_techs['end_use'] + " - " + runtime.existing_techs['description'] == nrcan_equiv].index.values[0] for nrcan_equiv in nrcan_equivs]

        for e in range(len(end_uses)):

            end_use = end_uses[e]
            nrcan_tech = nrcan_equivs[e]

            out_comm = runtime.end_use_demands.loc[end_use, 'comm']

            note = f"Assumed same as {nrcan_equivs[e]}"

            # Get annual capacity factor from equivalent nrcan tech for which we have data
            acf = curs.execute(
                f"""SELECT factor FROM {schema_models.LimitAnnualCapacityFactor.__table_name__}
                WHERE tech_or_group == '{nrcan_tech}'
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

            for vintage in runtime.cfg.future_periods:
                sql, params = schema_models.LimitAnnualCapacityFactor(
                    region=region,
                    tech_or_group=tech,
                    vintage=vintage,
                    output_comm=out_comm,
                    operator='ge',
                    factor=acf * 0.95,
                    notes=note,
                    data_source=ref.id,
                    dq_cred=1,
                    dq_geog=1,
                    dq_struc=3,
                    dq_tech=3,
                    dq_time=3,
                    data_id=utils.data_id(runtime, region),
                ).to_insert_or_ignore_sql()
                curs.execute(sql, params)
                sql, params = schema_models.LimitAnnualCapacityFactor(
                    region=region,
                    tech_or_group=tech,
                    vintage=vintage,
                    output_comm=out_comm,
                    operator='le',
                    factor=acf,
                    notes=note,
                    data_source=ref.id,
                    dq_cred=1,
                    dq_geog=1,
                    dq_struc=3,
                    dq_tech=3,
                    dq_time=3,
                    data_id=utils.data_id(runtime, region),
                ).to_insert_or_ignore_sql()
                curs.execute(sql, params)



# Doing all regions at once because some regions might share equivalent states and this process is slow
def aggregate_dsd(runtime: ResidentialRuntime, conn: sqlite3.Connection):

    print("Aggregating DSDs...")

    curs = conn.cursor() # Cursor object interacts with the sqlite db

    """
    ##############################################################
        Demand specific distribution
    ##############################################################
    """

    weather_year = runtime.cfg.weather_year
    reference = (f"{runtime.cfg.resstock.reference}; "
                 f"{runtime.cfg.weather.reference}; "
                 f"{runtime.cfg.nrcan_reference}; ")
    ref = runtime.refs.add('dsd', reference)

    res_config = pd.read_csv(runtime.cfg.input_files_dir + 'resstock.csv', index_col=0)
    cons = dict() # 8760 hourly energy consumption by state, housing type, and end use, (kWh)

    ## Get end use energy consumptions from resstock columns and divide by number of housing units represented
    for state in runtime.regions.loc[runtime.regions['include']]['us_state'].unique():

        cons[state] = dict()

        for housing_type, file_name in runtime.cfg.resstock.housing_files.items():
            cons[state][housing_type] = dict()

            df_res = nrcan.get_data(runtime.cfg.resstock.url.replace("<s>",state.upper()).replace("<f>", file_name).replace("<s>", state.lower()), cache_dir=runtime.cfg.cache_dir, force_download=runtime.cfg.force_download)
            df_res = df_res.fillna(0).set_index('timestamp')
            stock = df_res['units_represented'].iloc[0]

            for end_use in runtime.end_use_demands.index:

                res_cols = res_config.loc[res_config['end_use'] == end_use]

                for res_col in res_cols.index:

                    # Divide consumption by number of units represented to get consumption per household
                    con = df_res[res_col].iloc[[35039,*range(3,4*8760-3,4)]].astype(float).clip(lower=0) / float(stock) # 15-minutely so take every 4th

                    if end_use in cons[state][housing_type].keys(): cons[state][housing_type][end_use] += con
                    else: cons[state][housing_type][end_use] = con


    ## Multiply energy consumptions from resstock by province housing stocks, apply weather mapping, then normalise to DSD
    for region in runtime.cfg.province_list:

        data_id = utils.data_id(runtime, region)

        print(f"Aggregating DSDs for {region}...")

        row = runtime.regions.loc[region]
        state = row['us_state']

        note = (f"ResStock data for {state} (NREL, 2021) disaggregated by end use and building archetype and mapped from {state} {weather_year} air temperature "
                f"and humidity to {region} {weather_year} temperature and humidity, taking the mean of matched hours (Renewables Ninja, {weather_year}). "
                f"Reaggregated for {runtime.cfg.base_year} existing stock of housing archetypes in {region} (NRCan, {runtime.cfg.base_year})"
                f"Chronological linear interpolation for any missing data.")

        # Table 14: Total Households by Building Type and Energy Source
        t14 = nrcan.get_compr_db(region, 14, regions_df=runtime.regions, nrcan_url=runtime.cfg.nrcan_url, base_year=runtime.cfg.base_year, cache_dir=runtime.cfg.cache_dir, force_download=runtime.cfg.force_download, first_row=9, last_row=12)[runtime.cfg.base_year] / 100 # % shares

        # Create figure and axes
        fig, axs = pp.subplots(4, 3, figsize=(15, 10))  # 4 rows, 3 columns
        axs[-1, -1].axis('off')
        fig.tight_layout()
        fig.subplots_adjust(wspace=0.2, hspace=0.3, top=0.9, left=0.05, right=0.95, bottom=0.05)
        fig.suptitle(f"{region} demand specific distributions (blue). Weekly profile in red.")

        p = 0 # plot tracker
        for end_use, eud_config in runtime.end_use_demands.iterrows():

            demand_comm = eud_config['comm']

            # Consumption for each housing type times provincial stock of that housing type
            con_us = sum([t14[housing_type] * cons[state][housing_type][end_use] for housing_type in t14.index])
            con_us = utils.realign_timezone(con_us, from_timezone='EST', default_timezone=runtime.cfg.timezone)

            # Map space heating, cooling to temperature and dew point temp (humidity). Note: this might introduce weather efficiency to the demand!
            if eud_config['use_weather_map']: con_ca, time_of_week = weather_mapping.map_data(
                region,
                con_us.to_numpy(),
                runtime.regions.loc[region],
                runtime.cfg.cache_dir,
                runtime.cfg.weather_year,
                runtime.cfg.force_generate_weather_maps,
                runtime.cfg.weather.model_dump(),
                runtime.rninja_api,
            )
            else: con_ca = con_us

            # Apply tolerance and normalise
            con_ca.loc[con_ca < con_ca.mean() * runtime.cfg.dsd_tolerance] = 0
            dsd = (con_ca / con_ca.sum()).to_list()

            # For plotting DSDs
            row = p // 3 # integer division to determine row
            col = p % 3 # modulo to determine column
            axs[row, col].plot(dsd)
            #if eud_config['use_weather_map']: axs[row, col].twinx().plot(range(0,8736,52), time_of_week, 'r-') # time of week variation overlaid
            axs[row, col].set_title(end_use)
            p+=1

            rows = []
            for period in runtime.cfg.future_periods:
                for h, time in runtime.time.iterrows():

                    seas = time['season']
                    tod = time['tod']

                    if tod == runtime.time['tod'].iloc[0]:
                        rows.append(schema_models.DemandSpecificDistribution(
                            region=region,
                            period=period,
                            season=seas,
                            tod=tod,
                            demand_name=demand_comm,
                            dsd=dsd[h],
                            notes=note,
                            data_source=ref.id,
                            dq_cred=1,
                            dq_geog=3,
                            dq_struc=2,
                            dq_tech=1,
                            dq_time=3,
                            data_id=data_id,
                        ))
                    else:
                        rows.append(schema_models.DemandSpecificDistribution(
                            region=region,
                            period=period,
                            season=seas,
                            tod=tod,
                            demand_name=demand_comm,
                            dsd=dsd[h],
                            notes=None,
                            data_source=None,
                            dq_cred=None,
                            dq_geog=None,
                            dq_struc=None,
                            dq_tech=None,
                            dq_time=None,
                            data_id=data_id,
                        ))

            sql, params = schema_models.DemandSpecificDistribution.bulk_insert_or_ignore_sql(rows, include_nulls=True)
            curs.executemany(sql, params)

        pp.tight_layout()

    print(f"Demand specific distribution data aggregated into {os.path.basename(runtime.cfg.db_dir)}\n")



def aggregate_emissions(runtime: ResidentialRuntime, conn: sqlite3.Connection):

    curs = conn.cursor() # Cursor object interacts with the sqlite db


    """
    ##############################################################
        Emission Activity
    ##############################################################
    """

    emis_comm = runtime.cfg.emission_commodity
    emis_units = runtime.cfg.emission_activity_units

    ref = runtime.refs.add('epa', runtime.cfg.epa_reference)

    # Get emissions factors for fuels in ktCO2eq/PJ_in
    emis_fact = nrcan.get_data('https://www.epa.gov/system/files/other-files/2025-01/ghg-emission-factors-hub-2025.xlsx', cache_dir=runtime.cfg.cache_dir, force_download=runtime.cfg.force_download, skiprows=13, nrows=76, index_col=2)
    emis_fact = emis_fact[['CO2 Factor', 'CH4 Factor', 'N2O Factor']].iloc[1::].dropna()
    emis_fact = emis_fact[pd.to_numeric(emis_fact['CO2 Factor'], errors='coerce').notnull()] # Removing NaN rows
    for fact in emis_fact.columns: emis_fact[fact] = emis_fact[fact].astype(float) * runtime.cfg.conversion_factors.epa_units[fact.strip(' Factor')] * runtime.cfg.conversion_factors.gwp[fact.strip(' Factor')]
    emis_fact[emis_comm] = emis_fact.sum(axis=1)

    for tech in runtime.all_techs:

        # Valid vintages and efficiencies from Efficiency table
        rows = curs.execute(f"SELECT region, input_comm, tech, vintage, output_comm, efficiency FROM {schema_models.Efficiency.__table_name__} WHERE tech == '{tech}'").fetchall()

        for row in rows:

            # Input fuel by epa naming convention
            epa_fuel = runtime.fuel_commodities.loc[runtime.fuel_commodities['comm'] == row[1], 'epa_fuel'].iloc[0]
            if pd.isna(epa_fuel): continue # doesn't need emissions

            # EmissionActivity is tied to OUTPUT energy so divide by efficiency
            emis_act = emis_fact.loc[epa_fuel, emis_comm] / row[5]

            # Note assumed fuel
            note = f"Emissions factor using {epa_fuel} (EPA, {runtime.cfg.epa_year}) divided by efficiency as emissions are per output unit energy."

            sql, params = schema_models.EmissionActivity(
                region=row[0],
                emis_comm=emis_comm,
                input_comm=row[1],
                tech=row[2],
                vintage=row[3],
                output_comm=row[4],
                activity=emis_act,
                units=emis_units,
                notes=note,
                data_source=ref.id,
                dq_cred=1,
                dq_geog=3,
                dq_struc=2,
                dq_tech=4,
                dq_time=1,
                data_id=utils.data_id(runtime, row[0]),
            ).to_insert_or_ignore_sql()
            curs.execute(sql, params)

    print(f"Emissions data aggregated into {os.path.basename(runtime.cfg.db_dir)}\n")



def aggregate_imports(runtime: ResidentialRuntime, conn: sqlite3.Connection):

    curs = conn.cursor()

    # Get which fuel commodities are actually being used
    used_comms = set([c[0] for c in curs.execute(f"SELECT input_comm FROM {schema_models.Efficiency.__table_name__}").fetchall()])

    # Get the last period the import is used in this region and convert to a lifetime, to prevent supply orphans
    df_life = pd.read_sql_query(f"SELECT region, tech, lifetime FROM {schema_models.LifetimeTech.__table_name__}", conn).set_index(['region','tech']).astype(int)
    df_eff = pd.read_sql_query(f"SELECT region, input_comm, tech, vintage FROM {schema_models.Efficiency.__table_name__}", conn)
    df_eff = df_eff.loc[df_eff['tech'].isin(df_life.index.get_level_values('tech'))]
    df_eff['life'] = [
        row['vintage'] + df_life.loc[(row['region'], row['tech'])].iloc[0] - runtime.cfg.future_periods[0]
        for (_, row) in df_eff.iterrows()
    ]
    df_life = df_eff.groupby(['region','input_comm'])['life'].max().astype(int)

    for tech, row in runtime.import_techs.iterrows():

        # Get CANOE nomenclature for imported commodity
        out_comm = runtime.fuel_commodities.loc[row['out_comm']]

        # Make sure the model is using this imported commodity otherwise skip
        if out_comm['comm'] not in used_comms: continue

        description = f"import dummy for {out_comm['description']}"

        tech_kwargs = {
            'tech': tech,
            'flag': 'p',
            'sector': 'residential',
            'description': description,
            'data_id': utils.data_id(runtime),
        }
        for flag in row['flags'].split(','):
            tech_kwargs[flag.strip()] = 1
        sql, params = schema_models.Technology(**tech_kwargs).to_insert_or_ignore_sql()
        curs.execute(sql, params)

        for region in runtime.cfg.province_list:

            if (region, out_comm['comm']) not in df_life.index: continue

            # The dummy needs to retire when it is no longer used
            # (or it will be orphaned and removed by network checks)
            life = df_life.loc[(region, out_comm['comm'])]

            sql, params = schema_models.Efficiency(
                region=region,
                input_comm=runtime.fuel_commodities.loc[row['in_comm'], 'comm'],
                tech=tech,
                vintage=runtime.cfg.future_periods[0],
                output_comm=out_comm['comm'],
                efficiency=1,
                notes=description,
                data_id=utils.data_id(runtime, region),
            ).to_insert_or_ignore_sql()
            curs.execute(sql, params)

            if life < runtime.cfg.future_periods[-1] - runtime.cfg.future_periods[0]:
                sql, params = schema_models.LifetimeTech(
                    region=region,
                    tech=tech,
                    lifetime=life,
                    notes='(y) retires when no longer used',
                    data_id=utils.data_id(runtime, region),
                ).to_insert_or_ignore_sql()
                curs.execute(sql, params)

    print(f"Imports aggregated into {os.path.basename(runtime.cfg.db_dir)}\n")



def cleanup(runtime: ResidentialRuntime, conn: sqlite3.Connection):

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

    for region in runtime.cfg.province_list:
        for tech, row in runtime.existing_techs.iterrows():
            if row['end_use'] == 'appliances other': continue # Does not have capacity

            exs_cap = curs.execute(f"SELECT sum(capacity) FROM {schema_models.ExistingCapacity.__table_name__} WHERE tech == '{tech}' and region == '{region}'").fetchone()[0]
            if not exs_cap or exs_cap < runtime.cfg.existing_cap_tolerance:

                # If no existing capacity for an existing tech, purge tech/region combo from database
                for table in rt_tables:
                    curs.execute(f"DELETE FROM '{table}' WHERE tech == '{tech}' AND region == '{region}'")

                print(f"Cleaned up existing region-tech with little or no existing capacity: ({region}, {tech})")

    for tech, row in runtime.existing_techs.iterrows():
        if row['end_use'] == 'appliances other': continue # Does not have capacity

        exs_cap = curs.execute(f"SELECT sum(capacity) FROM {schema_models.ExistingCapacity.__table_name__} WHERE tech == '{tech}'").fetchone()[0]
        if not exs_cap or exs_cap == 0:

            # If no existing capacity for an existing tech, purge tech/region combo from database
            for table in t_tables:
                curs.execute(f"DELETE FROM '{table}' WHERE tech == '{tech}'")

            print(f"Cleaned up existing tech with no existing capacity: {tech}")

    print(f"Cleanup complete.\n")



if __name__ == "__main__":
    from canoe_residential.setup import build_runtime
    import sqlite3 as _sqlite3
    rt = build_runtime()
    with _sqlite3.connect(rt.cfg.db_dir) as _conn:
        aggregate(rt, _conn)
