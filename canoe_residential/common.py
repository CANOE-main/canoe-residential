"""
Common types, config model, and runtime container for canoe-residential.

CANOEResidentialConfig  — typed, YAML-loadable configuration (Pydantic).
ResidentialRuntime      — bundles cfg with loaded DataFrames and mutable
                          runtime state; passed to every subsector function.

Usage:
    from canoe_residential.setup import build_runtime
    runtime = build_runtime()
    with sqlite3.connect(runtime.cfg.db_dir) as conn:
        all_subsectors.aggregate(runtime, conn)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Sub-models for nested config sections
# ---------------------------------------------------------------------------

class LightingConvConfig(BaseModel):
    efficacy: float
    lifetime: float
    cost: float


class ActivityConvConfig(BaseModel):
    kwh: float


class CostConvConfig(BaseModel):
    invest: float
    fixed: float


class ConversionFactors(BaseModel):
    """Unit conversion constants.

    epa_units, gwp, and efficiency use dict[str, float] because they are
    accessed with runtime-determined keys (gas name, efficiency metric name).
    """
    epa_units: dict[str, float]
    gwp: dict[str, float]
    efficiency: dict[str, float]
    lighting: LightingConvConfig
    activity: ActivityConvConfig
    cost: CostConvConfig


class FurnaceFanConfig(BaseModel):
    output_split: float
    efficiency: float
    data_year: int
    reference: str


class AppliancesConfig(BaseModel):
    annual_capacity_factor: float


class LightingConfig(BaseModel):
    annual_capacity_factor: float
    acf_reference: str
    acf_data_year: int
    acf_note: str
    on_stock_ref: str
    usage_ref: str
    input_comm: str


class ResStockConfig(BaseModel):
    url: str
    housing_files: dict[str, str]
    reference: str


class WeatherConfig(BaseModel):
    us_temperature_url: str
    us_humidity_url: str
    ca_temperature_url: str
    ca_humidity_url: str
    reference: str


class DataQualityProfile(BaseModel):
    """Data quality scores for one data-construction step, spread into each row.

    Set one instance per logical data source / construction step so the
    justification for each score lives in one place.
    """
    dq_cred: int
    dq_geog: int
    dq_struc: int
    dq_tech: int
    dq_time: int


class AeoSheetConfig(BaseModel):
    """Row/column offsets for an rsmess.xlsx sheet.

    Previously hard-coded in setup.py as magic numbers; now config fields
    so they can be overridden in params.yaml without editing source code.
    """
    skiprows: int
    nrows: int
    index_col: int
    row_slice_start: int = 1
    col_slice_start: int = 1
    col_slice_end: int | None = None


# ---------------------------------------------------------------------------
# Reference / bibliography (legacy — TODO replace with DataSource WS2)
# ---------------------------------------------------------------------------

class reference:
    """Single citable source with a short ID and full citation string."""
    id: str
    citation: str

    def __init__(self, id: str, citation: str):
        self.id = id
        self.citation = citation


class bibliography:
    """Accumulates references across all subsectors; written to DataSource in post_process."""

    def __init__(self):
        self.references: dict[str, reference] = {}

    def __iter__(self):
        yield from self.references.values()

    def add(self, name: str, citation: str) -> reference | None:
        if name in self.references:
            return self.references[name]
        num = len(self.references) + 1
        ref_id = f"R{num:02d}"
        ref = reference(id=ref_id, citation=citation)
        self.references[name] = ref
        return ref

    def get(self, name: str) -> reference | None:
        if name not in self.references:
            print(f"Tried to get a reference that had not been added yet: {name}")
            return None
        return self.references[name]


# ---------------------------------------------------------------------------
# Main config model
# ---------------------------------------------------------------------------

class CANOEResidentialConfig(BaseModel):
    """Typed, YAML-loadable configuration for canoe-residential.

    Loaded via `CANOEResidentialConfig.validate_from_yaml(path)`.
    DataFrames, mutable runtime state, and network-fetched data live
    separately in `ResidentialRuntime`.
    """
    model_config = ConfigDict(extra="forbid")

    # --- Shared base fields (mirror CANOEAgricultureConfig; candidates for
    #     a future canoe-common package) ---
    schema_version: str = "4.0"
    db_dir: str                              # path to SQLite database file
    existing_periods: list[int] = []         # populated from DB; not in YAML
    future_periods: list[int]                # = sorted(model_periods) from YAML
    # TODO replace with CANOEProvince class
    province_list: list[str] = []            # populated from regions.csv at runtime
    validation_behavior: Literal["error", "warning"] = "error"

    # --- Provenance ---
    version: str                             # = data_version in YAML
    data_id_prefix: str

    # --- Time / region ---
    base_year: int
    period_step: int
    weather_year: int
    timezone: str
    aeo_data_year: int
    aeo_currency_year: int
    aeo_currency: str
    statcan_data_year: int

    # --- File paths ---
    input_files_dir: str = "input_files/"
    cache_dir: str = "data_cache/"
    excel_template: str
    excel_output: str

    # --- AEO spreadsheet offsets (WS4 priority item 2) ---
    # Previously hard-coded in setup.py; now overridable from params.yaml.
    aeo_rsclass: AeoSheetConfig = AeoSheetConfig(
        skiprows=19, nrows=31, index_col=20, row_slice_start=1, col_slice_start=1, col_slice_end=20
    )
    aeo_rsmeqp: AeoSheetConfig = AeoSheetConfig(
        skiprows=21, nrows=867, index_col=29, row_slice_start=2, col_slice_start=2, col_slice_end=29
    )

    # --- Scalar settings ---
    global_discount_rate: float
    existing_cap_tolerance: float
    dsd_tolerance: float

    # --- Currency ---
    final_currency: str
    final_currency_year: int
    inflation_index: str

    # --- Aggregation switches ---
    force_generate_weather_maps: bool = False
    force_download: bool = False
    show_plots: bool = True
    include_dsd: bool = True
    include_imports: bool = False
    include_emissions: bool = False
    include_furnace_fans: bool = False
    simplify_model: bool = False
    clone_to_xlsx: bool = False

    # --- Scenario selectors / data source references (WS4 priority item 3) ---
    nrcan_url: str
    nrcan_reference: str
    handbook_reference: str
    aeo_reference: str
    aeo_updated_reference: str
    statcan_reference: str
    epa_reference: str
    epa_year: int
    epa_url: str
    emission_commodity: str
    emission_activity_units: str

    # --- Structured sub-models ---
    conversion_factors: ConversionFactors
    furnace_fans: FurnaceFanConfig
    appliances: AppliancesConfig
    lighting: LightingConfig
    resstock: ResStockConfig
    weather: WeatherConfig
    housing_categories: dict[str, str]

    # --- Default data-quality profile (WS4 priority item 4) ---
    # TODO set per-table justifications when writing each row batch
    dq_defaults: DataQualityProfile = DataQualityProfile(
        dq_cred=2, dq_geog=2, dq_struc=2, dq_tech=2, dq_time=2
    )

    @classmethod
    def validate_from_yaml(cls, yaml_path: str) -> "CANOEResidentialConfig":
        """Load and validate config from params.yaml, mapping renamed fields."""
        with open(yaml_path, "r") as f:
            raw: dict = yaml.safe_load(f)

        # Renames: params.yaml key → CANOEResidentialConfig field name
        raw["db_dir"] = raw.pop("sqlite_database")
        raw["future_periods"] = sorted(raw.pop("model_periods"))
        raw["version"] = raw.pop("data_version")

        # Fields not present in YAML — use defaults from model
        raw.setdefault("province_list", [])
        raw.setdefault("existing_periods", [])

        return cls.model_validate(raw)


# ---------------------------------------------------------------------------
# Runtime container
# ---------------------------------------------------------------------------

@dataclass
class ResidentialRuntime:
    """Bundles CANOEResidentialConfig with loaded DataFrames and runtime state.

    Created by `setup.build_runtime()`. Passed to every subsector aggregate()
    function alongside a `sqlite3.Connection` (opened by the caller).

    Mutable fields (`tech_vints`, `lifetimes`, `refs`, `data_ids`) are written
    during `all_subsectors.pre_process()` and read by the subsector modules.
    """

    cfg: CANOEResidentialConfig

    # --- Static data loaded from input files ---
    existing_techs: pd.DataFrame
    new_techs: pd.DataFrame
    import_techs: pd.DataFrame
    regions: pd.DataFrame
    fuel_commodities: pd.DataFrame
    end_use_demands: pd.DataFrame
    time: pd.DataFrame
    all_techs: list
    aeo_res_class: pd.DataFrame
    aeo_res_equip: pd.DataFrame
    populations: pd.DataFrame
    rninja_api: str

    # --- Currency conversion data ---
    currency_exchange: pd.DataFrame
    currency_inflation: pd.DataFrame

    # --- Mutable runtime state ---
    # Written during pre_process(), read by subsectors.
    tech_vints: dict = field(default_factory=dict)
    lifetimes: dict = field(default_factory=dict)
    refs: bibliography = field(default_factory=bibliography)
    data_ids: set = field(default_factory=set)
