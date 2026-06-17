"""
Loads configuration and data for canoe-residential.

The `config` singleton is replaced by `build_runtime()`, which returns a
`ResidentialRuntime` containing the typed `CANOEResidentialConfig` plus
all DataFrames and mutable runtime state needed by the subsectors.

Typical usage (from residential_sector.py):
    runtime = build_runtime()
    with sqlite3.connect(runtime.cfg.db_dir) as conn:
        all_subsectors.aggregate(runtime, conn)
"""

import os
from pathlib import Path

import pandas as pd

import canoe_residential.statcan as statcan
from canoe_residential.common import (
    CANOEResidentialConfig,
    ResidentialRuntime,
    bibliography,
)

_DEFAULT_PARAMS = "input_files/params.yaml"


def build_runtime(yaml_path: str = _DEFAULT_PARAMS) -> ResidentialRuntime:
    """Load config + data files and return a populated ResidentialRuntime.

    Args:
        yaml_path: path to params.yaml (relative to the working directory).
    """
    cfg = CANOEResidentialConfig.validate_from_yaml(yaml_path)

    input_dir = Path(cfg.input_files_dir)
    cache_dir = cfg.cache_dir

    # --- Input CSVs ---
    existing_techs = pd.read_csv(input_dir / "existing_technologies.csv", index_col=0)
    new_techs = pd.read_csv(input_dir / "new_technologies.csv", index_col=0)
    import_techs = pd.read_csv(input_dir / "import_technologies.csv", index_col=0)
    regions = pd.read_csv(input_dir / "regions.csv", index_col=0)
    fuel_commodities = pd.read_csv(input_dir / "fuel_commodities.csv", index_col=0)
    end_use_demands = pd.read_csv(input_dir / "end_use_demands.csv", index_col=0)
    time_df = pd.read_csv(input_dir / "time.csv", index_col=0)

    all_techs = [*new_techs.index.values, *existing_techs.index.values]

    # province_list: regions included in the run, from regions.csv
    province_list = sorted(
        regions.loc[regions["include"]].index.unique().tolist()
    )
    cfg = cfg.model_copy(update={"province_list": province_list})

    # --- AEO spreadsheet (priority item 2: offsets now in config) ---
    rs = cfg.aeo_rsclass
    aeo_res_class = pd.read_excel(
        input_dir / "rsmess.xlsx",
        sheet_name="RSCLASS",
        skiprows=rs.skiprows,
        nrows=rs.nrows,
        index_col=rs.index_col,
    ).iloc[rs.row_slice_start:, rs.col_slice_start:rs.col_slice_end]

    rq = cfg.aeo_rsmeqp
    aeo_res_equip = pd.read_excel(
        input_dir / "rsmess.xlsx",
        sheet_name="RSMEQP",
        skiprows=rq.skiprows,
        nrows=rq.nrows,
        index_col=rq.index_col,
    ).iloc[rq.row_slice_start:, rq.col_slice_start:rq.col_slice_end]

    # --- Population projections (StatCan) ---
    populations = statcan.load_population_projections(
        regions, cache_dir, cfg.force_download
    )

    # --- Renewables Ninja API token ---
    try:
        with open("input_files/rninja_api_token.txt") as f:
            rninja_api = f.read().strip()
    except FileNotFoundError:
        rninja_api = ""

    # --- Currency conversion tables ---
    currency_exchange = pd.read_csv(input_dir / "currency_exchange.csv", index_col=0)
    currency_inflation = pd.read_csv(input_dir / "cad_inflation.csv", index_col=0)

    # --- Pre-populate commonly used references ---
    refs = bibliography()
    refs.add("nrcan", cfg.nrcan_reference)
    refs.add("aeo", cfg.aeo_reference)
    refs.add("statcan", cfg.statcan_reference)
    refs.add("nrcan_statcan", f"{cfg.nrcan_reference}; {cfg.statcan_reference}")

    os.makedirs(cache_dir, exist_ok=True)

    print("Loaded canoe-residential config and data files.\n")

    return ResidentialRuntime(
        cfg=cfg,
        existing_techs=existing_techs,
        new_techs=new_techs,
        import_techs=import_techs,
        regions=regions,
        fuel_commodities=fuel_commodities,
        end_use_demands=end_use_demands,
        time=time_df,
        all_techs=all_techs,
        aeo_res_class=aeo_res_class,
        aeo_res_equip=aeo_res_equip,
        populations=populations,
        rninja_api=rninja_api,
        currency_exchange=currency_exchange,
        currency_inflation=currency_inflation,
        refs=refs,
    )
