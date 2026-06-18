"""
Reduces residential sector from full resolution to simple version
Written by Ian David Elder for the CANOE model
"""

import sqlite3
# from canoe_residential.setup import config


def simplify_model():

    # Connect to the new database file
    conn = sqlite3.connect(config.database_file)
    curs = conn.cursor() # Cursor object interacts with the sqlite db

    for region in config.model_regions:
        for tech in config.all_techs:
            if tech == 'R_APP_OTH': continue # No associated costs
            
            # Get basic parameters from full resolution model
            vints = config.tech_vints[tech]
            life = curs.execute(f"SELECT life FROM LifetimeTech WHERE regions == '{region}' AND tech == '{tech}'").fetchone()[0]
            acf = curs.execute(f"SELECT max_acf FROM MaxAnnualCapacityFactor WHERE regions == '{region}' AND tech == '{tech}'").fetchone()[0]
            c2a = curs.execute(f"SELECT c2a FROM CapacityToActivity WHERE regions == '{region}' AND tech == '{tech}'").fetchone()[0]

            # Need to know annual activity to calculate levelised cost of activity
            annual_act = c2a * acf

            for vint in vints:
                
                # Amortise capital cost over the lifetime of the technology using global discount rate
                cost_invest = curs.execute(f"SELECT data_cost_invest FROM CostInvest WHERE regions == '{region}' AND tech == '{tech}' AND vintage == {vint}").fetchone()
                cost_invest = cost_invest[0] if cost_invest is not None else 0
                i = config.params['global_discount_rate']
                annuity = cost_invest * i * (1+i)**life / ((1+i)**life - 1)

                # Get fixed cost from table
                cost_fixed = curs.execute(f"SELECT data_cost_fixed FROM CostFixed WHERE  regions == '{region}' AND tech == '{tech}' and vintage == {vint}").fetchone()
                cost_fixed = cost_fixed[0] if cost_fixed is not None else 0

                # Levelised cost of activity is annual fixed O&M plus annualised capital cost divided by annual activity
                lcoa = (cost_fixed + annuity) / annual_act
                
                if lcoa == 0: continue # No associated cost

                # Add LCOA as a variable cost
                for period in config.model_periods:
                    if vint > period or vint + life <= period: continue

                    curs.execute(f"""REPLACE INTO
                                CostVariable(regions, periods, tech, vintage, data_cost_variable, data_cost_year, data_curr)
                                VALUES('{region}', {period}, '{tech}', {vint}, {lcoa}, {2022}, '{"USD"}')""")
    
    # Only one time period per year: S01, D01
    curs.execute(f"INSERT OR IGNORE INTO time_periods(t_periods, flag) VALUES({config.model_periods[0]-1}, 'e')") # needs one existing period
    curs.execute(f"DELETE FROM time_season")
    curs.execute(f"DELETE FROM time_of_day")
    curs.execute(f"DELETE FROM SegFrac")
    curs.execute(f"INSERT OR IGNORE INTO time_season(t_season) VALUES('S01')")
    curs.execute(f"INSERT OR IGNORE INTO time_of_day(t_day) VALUES('D01')")
    curs.execute(f"INSERT OR IGNORE INTO SegFrac(season_name, time_of_day_name, segfrac) VALUES('S01', 'D01', 1)")

    # Set DSD to 1 for one annual time period
    for region in config.model_regions:         
        for end_use, row in config.end_use_demands.iterrows():
            curs.execute(f"""REPLACE INTO
                        DemandSpecificDistribution(regions, season_name, time_of_day_name, demand_name, dsd)
                        VALUES('{region}', 'S01', 'D01', '{row['comm']}', 1)""")
            
    # Remove unused commodities
    curs.execute(f"DELETE FROM commodities WHERE comm_name == 'R_ethos' OR comm_name == 'CO2eq'")

    # Clear unnecessary data
    curs.execute(f"DELETE FROM CostFixed")
    curs.execute(f"DELETE FROM CostInvest")
    curs.execute(f"DELETE FROM CapacityToActivity")
    curs.execute(f"DELETE FROM MinAnnualCapacityFactor")
    curs.execute(f"DELETE FROM MaxAnnualCapacityFactor")

    # Remove existing capacity
    curs.execute(f"DELETE FROM ExistingCapacity WHERE tech like '%EXS'")
    curs.execute(f"DELETE FROM Efficiency WHERE tech like '%EXS'")
    curs.execute(f"DELETE FROM LifetimeTech WHERE tech like '%EXS'")
    curs.execute(f"DELETE FROM technologies WHERE tech like '%EXS'")

    conn.commit()
    conn.close()