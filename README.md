# canoe-residential

This script aggregates the modular residential sector model for CANOE. It pulls data primarily from the NRCan Comprehensive Energy Use Database and from Annual Energy Outlook technology assumptions.

Aggregation can be configured through files in `input_files/` to include/exclude technologies, provinces, types of data/model structures.

It will download a large number of files on the first run, but will cache these files locally and use the local cache in subsequent runs. Parameters can be set to force downloading to get latest data.

## Usage

### 1. Create the conda environment

1. Install miniconda
2. Open a miniconda prompt
3. Install the conda environment:
   ```bash
   cd canoe-residential
   conda env create
   ```
   (This creates an environment named `canoe-backend`)

### 2. Run the aggregation

1. Activate the environment:
   ```bash
   conda activate canoe-backend
   ```
2. Run the script:
   ```bash
   python .
   ```
   Alternatively, if you are in the parent directory:
   ```bash
   python canoe-residential/
   ```

## Components

### Directories

- **data_cache/**: Where downloaded data is locally cached and pulled from on subsequent runs, unless `force_download` param is set to True.
- **docs/**: Directory of documents describing the operation of this sector backend.
- **input_files/**:
  - Configuration files
  - CANOE database schema
  - CANOE excel spreadsheet template
  - `params.yaml` (see Configuration)

### Scripts

- **`__main__.py`**: Entry point for running from command line.
- **`residential_sector.py`**: Handles execution order of other scripts.
- **`currency_conversion.py`**: Converts data currencies to a unified final currency.
- **`setup.py`**: Builds the config object which contains aggregation configuration and some common data.
- **`utils.py`**: Repository of frequently used utility scripts. Includes `get_data()` method which robustly fetches and locally caches data from online sources.
- **`model_reduction.py`**: Reduces residential sector from full resolution to simple version.
- **`testing_dummy.py`**: A blank script for prototyping code snippets.
- **`all_subsectors.py`**: Aggregation scripts for subsectors.

## Configuration

Configuration is performed by editing the files in `input_files/`.

### `params.yaml`

Contains various aggregation parameters. Some cannot be changed but others can. Generally free to play with booleans under "## Aggregation switches".
