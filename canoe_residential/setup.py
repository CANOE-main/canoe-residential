"""
Sets up configuration for buildings sector aggregation
Written by Ian David Elder for the CANOE model
"""

import os
import pandas as pd
import yaml

import canoe_residential.statcan as statcan



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
    _this_dir = "./"
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

        config.populations = statcan.load_population_projections(
            config.regions, config.cache_dir, config.params['force_download']
        )



    def _get_rninja_api(cls):

        with open('input_files/rninja_api_token.txt') as token_file:
            token = token_file.read()
        config.rninja_api = token
        


# Instantiate on import
config()
