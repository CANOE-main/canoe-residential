"""
Sets up configuration for buildings sector aggregation
Written by Ian David Elder for the CANOE model
"""

import os
import pandas as pd
import yaml
import requests
import urllib.request
import zipfile
import sqlite3



def instantiate_database():
    
    # Check if database exists or needs to be built
    build_db = not os.path.exists(config.database_file)

    # Connect to the new database file
    conn = sqlite3.connect(config.database_file)
    curs = conn.cursor() # Cursor object interacts with the sqlite db

    # Build the database if it doesn't exist. Otherwise clear all data if forced
    if build_db: curs.executescript(open(config.schema_file, 'r').read())
    elif config.params['force_wipe_database']:
        tables = [t[0] for t in curs.execute("""SELECT name FROM sqlite_master WHERE type='table';""").fetchall()]
        for table in tables: curs.execute(f"DELETE FROM '{table}'")
        curs.executescript(open(config.schema_file, 'r').read())
        print("Database wiped prior to aggregation. See params.\n")

    conn.commit()

    # VACUUM operation to clean up any empty rows
    conn.execute("VACUUM;")
    conn.commit()

    conn.close()



class reference:
    """
    Stores a single reference and its attributes
    - id: the unique id for the source_id column
    - citation: the full citation to go in the DataSource table
    """

    id: str
    citation: str

    def __init__(self, id: str, citation: str):
        self.id = id
        self.citation = citation


class bibliography:
    """This class stores references and handles unique indexing"""

    references: dict[str, reference] = dict()

    def __iter__(self):
        for name, ref in self.references.items():
            yield ref

    def add(cls, name: str, citation: str) -> reference | None:
        """Add a reference to the log and return the reference object"""

        if name in cls.references:
            return cls.references[name]
        else:
            num = len(cls.references.keys()) + 1
            id = f"R{num}" if num >= 10 else f"R0{num}" # R01 -> R99 unique IDs
            ref = reference(id=id, citation=citation)
            cls.references[name] = ref
            return ref
    
    def get(cls, name: str) -> reference | None:
        """Returns a reference by its semantic name"""

        if name not in cls.references:
            print(f"Tried to get a reference that had not been added yet: {name}")
            return
        else:
            return cls.references[name]



class config:

    # File locations
    _this_dir = os.path.realpath(os.path.dirname(__file__)) + "/"
    input_files = _this_dir + 'input_files/'
    cache_dir = _this_dir + "data_cache/"

    if not os.path.exists(cache_dir): os.mkdir(cache_dir)

    refs: bibliography = bibliography()
    data_ids = set()

    tech_vints = {}
    lifetimes = {}

    _instance = None # singleton pattern


    def __new__(cls, *args, **kwargs):

        if isinstance(cls._instance, cls): return cls._instance
        cls._instance = super(config, cls).__new__(cls, *args, **kwargs)

        cls._get_params(cls._instance)
        cls._get_files(cls._instance)
        cls._get_aeo_data(cls._instance)
        cls._get_population_projections(cls._instance)
        cls._get_rninja_api(cls._instance)
        cls._add_references(cls._instance)

        print('Instantiated setup config.\n')

        return cls._instance


    def _get_params(cls):
        
        stream = open(config.input_files + "params.yaml", 'r')
        config.params = dict(yaml.load(stream, Loader=yaml.Loader))

        config.new_techs = pd.read_csv(config.input_files + 'new_technologies.csv', index_col=0)
        config.existing_techs = pd.read_csv(config.input_files + 'existing_technologies.csv', index_col=0)
        config.import_techs = pd.read_csv(config.input_files + 'import_technologies.csv', index_col=0)
        config.regions = pd.read_csv(config.input_files + 'regions.csv', index_col=0)
        config.fuel_commodities = pd.read_csv(config.input_files + 'fuel_commodities.csv', index_col=0)
        config.end_use_demands = pd.read_csv(config.input_files + 'end_use_demands.csv', index_col=0)
        config.time = pd.read_csv(config.input_files + 'time.csv', index_col=0)

        config.all_techs = [*config.new_techs.index.values, *config.existing_techs.index.values]

        # Included regions and future periods
        config.model_periods = list(config.params['model_periods'])
        config.model_periods.sort()
        config.model_regions = config.regions.loc[(config.regions['include'])].index.unique().to_list()
        config.model_regions.sort()
        


    def _get_files(cls):

        config.schema_file = config.params['sqlite_schema']
        config.database_file = config.params['sqlite_database']
        config.excel_template_file = config.params['excel_template']
        config.excel_target_file = config.params['excel_output']



    def _get_aeo_data(cls):

        config.aeo_res_class = pd.read_excel(config.input_files + 'rsmess.xlsx',
                                             sheet_name='RSCLASS', skiprows=19, nrows=31, index_col=20).iloc[1:,1:20]
        config.aeo_res_equip = pd.read_excel(config.input_files + 'rsmess.xlsx',
                                             sheet_name='RSMEQP', skiprows=21, nrows=867, index_col=29).iloc[2:,2:29]
        

    
    def _add_references(cls):
        """Adds some very commonly used references to the bibliography"""
        config.refs.add('nrcan', config.params['nrcan_reference'])
        config.refs.add('aeo', config.params['aeo_reference'])
        config.refs.add('statcan', config.params['statcan_reference'])
        config.refs.add('nrcan_statcan', f"{config.params['nrcan_reference']}; {config.params['statcan_reference']}")
        

    
    def _get_population_projections(cls) -> pd.DataFrame:

        config.populations = dict()

        # Get historical population data from Statcan and take Q1
        df_exs = config._get_statcan_table(17100009, usecols=[0,1,9])
        df_exs = df_exs.loc[df_exs['REF_DATE'].str.contains('-01')]
        df_exs['REF_DATE'] = df_exs['REF_DATE'].str.removesuffix("-01")

        # Get projected population data from Statcan for M1 scenario
        df_proj = config._get_statcan_table(17100057, usecols=[0,1,3,4,5,12])
        df_proj['VALUE'] *= 1000
        df_proj = df_proj.loc[
            (df_proj['Projection scenario'] == 'Projection scenario M1: medium-growth') & 
            (df_proj['Gender'] == 'Total - gender') &
            (df_proj['Age group'] == 'All ages')]

        # For each region, take historical first, then provincial, then index to Canadian when that runs out
        for region, row in config.regions.iterrows():

            if not row ['include']: continue
            
            # Existing data
            exs = df_exs.loc[df_exs['GEO'].str.upper() == row['description'].upper()].dropna()

            # Projected provincial data
            prov = df_proj.loc[(df_proj['GEO'].str.upper() == row['description'].upper()) &
                            (df_proj['REF_DATE'] > int(exs['REF_DATE'].values[-1]))].dropna()
            
            # Index missing provincial data to Canadian projections
            ca = df_proj.loc[(df_proj['GEO'].str.upper() == 'CANADA') & 
                            (df_proj['REF_DATE'] >= int(prov['REF_DATE'].values[-1]))].dropna()
            ca['VALUE'] = ca['VALUE'].iloc[1::] * prov['VALUE'].values[-1] / ca['VALUE'].values[0]
            ca.dropna(inplace=True)

            # Create dataframe of population for all years
            data = [*exs['VALUE'].to_list(), *prov['VALUE'].to_list(), *ca['VALUE'].to_list()]
            pop = pd.DataFrame(index = range(int(exs['REF_DATE'].values[0]), int(ca['REF_DATE'].values[-1]+1)), data = [int(d) for d in data], columns=['population'])
            pop.index.rename('year', inplace=True)

            # Add to dictionary of regional population projections
            config.populations[region] = pop

        

    # Have to put this here or it's awkward circular imports with utils
    def _get_statcan_table(table, save_as=None, **kwargs):
        
        if save_as == None: save_as = f"statcan_{table}.csv"
        if os.path.splitext(save_as)[1] != ".csv": save_as += ".csv"

        if not config.params['force_download']  and os.path.isfile(config.cache_dir + save_as):

            try:

                df = pd.read_csv(config.cache_dir + save_as, index_col=0)
                
                print(f"Got Statcan table {table} from local cache.")
                return df
            
            except Exception as e:

                print(f"Could not get Statcan table {table} from local cache. Trying to download instead.")

        # Make a request from the API for the table, returns response status and url for download
        url = f"https://www150.statcan.gc.ca/t1/wds/rest/getFullTableDownloadCSV/{table}/en"
        response = requests.get(url).json()

        # If successful, download the table
        if response['status'] == 'SUCCESS':

            print(f"Downloading Statcan table {table}...")

            # Download and open the zip file
            filehandle,_ = urllib.request.urlretrieve(response['object'])
            zip_file_object = zipfile.ZipFile(filehandle, 'r')

            # Read the table from inside the zip file
            from_file = zip_file_object.open(f"{table}.csv", "r")
            df = pd.read_csv(from_file, **kwargs)
            from_file.close()

            df.to_csv(config.cache_dir + save_as)

            print(f"Cached Statcan table {table}.")
            return df

        else:

            print(f"Request for {table} from Statcan failed. Status: {response['status']}")
            return None
        


    def _get_rninja_api(cls):

        with open('input_files/rninja_api_token.txt') as token_file:
            token = token_file.read()
        config.rninja_api = token
        


# Instantiate on import
config()
