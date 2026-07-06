# Developer notes

## prep_high_res_testing (removed in Stage 2 refactor)

The function below was removed from `residential_sector.py` in the Stage 2
refactor because it writes directly to B-category tables (`time_season`,
`SegFrac`) that are owned by canoe-base — incompatible with the contract that
canoe-residential opens an existing DB and never mutates shared structure.

If reduced-resolution testing is needed again, the correct approach is:
- Ask canoe-base to populate `time_season`/`SegFrac` with only the desired
  representative days before running canoe-residential; or
- Add a scoped `DELETE WHERE data_id IN (...)` step to canoe-residential
  that only touches rows written by this module (C-category tables), never
  B-category rows.

```python
def prep_high_res_testing():
    """
    REMOVED — mutates B-category tables (time_season, SegFrac) globally.
    Kept here for reference only; do not restore without refactoring to
    avoid writes to shared structure tables.
    """

    conn = sqlite3.connect(config.database_file)
    curs = conn.cursor()
    
    fuel_costs = {
        "NG": 8.847,
        "OIL": 25.163,
        "ELC": 31.944,
        "WOOD": 17.38,
        "LPG": 49.88
    }

    base_emis = {
        "ON": 16800,
        "AB": 8700,
        "BC": 4300,
        "MB": 1200,
        "SK": 1900,
        "QC": 3100
    }

    emis = {
        2021: 1,
        2025: 0.80,
        2030: 0.60,
        2035: 0.45,
        2040: 0.3,
        2045: 0.15,
        2050: 0
    }
                
    rep_days = [
        'D006', # Coldest day ON 2018
        'D035',
        'D070',
        'D105',
        'D140',
        'D186' # Hottest day ON 2018
    ]

    seas_tables = [
        'DemandSpecificDistribution'
    ]

    # Delete all days but rep days above
    curs.execute(f"DELETE FROM time_season")
    [curs.execute(f"INSERT OR IGNORE INTO time_season(t_season) VALUES('{day}')") for day in rep_days]

    for table in seas_tables:
        curs.execute(f"DELETE FROM {table} WHERE season_name NOT IN (SELECT t_season from time_season)")

    curs.execute(f"DELETE FROM SegFrac")
    for day in rep_days:
        for h in range(24):
            curs.execute(f"""REPLACE INTO SegFrac(season_name, time_of_day_name, segfrac)
                        VALUES('{day}', '{config.time.loc[h, 'time_of_day']}', {1/(24*6)})""")
            
    # Renormalise dsd
    for end_use in config.end_use_demands['comm']:
        for region in config.model_regions:
            total_dsd = sum([dsd[0] for dsd in curs.execute(f"""SELECT dsd FROM DemandSpecificDistribution
                                                            WHERE demand_name == '{end_use}' AND regions == '{region}'""").fetchall()])
            curs.execute(f"""UPDATE DemandSpecificDistribution
                        SET dsd = dsd / {total_dsd}
                        WHERE demand_name == '{end_use}' and regions = '{region}'""")

    # Add fuel imports and costs
    for fuel, cost in fuel_costs.items():
            for period in config.model_periods:
                curs.execute(f"""REPLACE INTO
                            CostVariable(regions, periods, tech, vintage, cost_variable, cost_variable_units, data_cost_year, data_curr, data_flags)
                            VALUES('{region}', {period}, 'R_IMP_{fuel}', {config.model_periods[0]}, {cost}, 'TEST VAL M$/PJ', 2020, 'CAD', 'TEST')""")

    for region in config.model_regions:
        for period in config.model_periods:
            curs.execute(f"""REPLACE INTO
                        EmissionLimit(regions, periods, emis_comm, emis_limit, emis_limit_units)
                        VALUES('{region}', {period}, "CO2eq", {emis[period]*base_emis[region]}, "ktCO2eq")""")
    
    conn.commit()
    conn.execute("VACUUM;")
    
    conn.commit()
    conn.close()

    print("Finished.")
```
