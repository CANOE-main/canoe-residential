# External data sources

Human-reference list of external sources hit by `canoe-residential`'s data
acquisition modules (`statcan.py`, `nrcan.py`, `weather_mapping.py`). Not a
machine-readable manifest — see `design_docs/CANOE_module_refactor_design.md`
§4.

| Source | What it provides | Accessed by | Cache file |
|---|---|---|---|
| NRCan Comprehensive Energy Use Database (CEUD), residential "COMPR-DB" tables | Sector energy use, equipment stocks and efficiencies by province/technology/year | `nrcan.get_compr_db` (via `space_heating.py`, `space_cooling.py`, `water_heating.py`, `appliances.py`, `lighting.py`, `all_subsectors.py`) | `<cache_dir>/<url-derived filename>.csv` |
| NRCan Handbook on Energy Consumption (e.g. `res_00_16_e.xls`) | Unit energy consumption (UEC) by appliance type | `nrcan.get_data` (via `appliances.py`) | `<cache_dir>/res_00_16_e.csv` |
| Statistics Canada table 17-10-0009 | Historical quarterly provincial population | `statcan.load_population_projections` (via `setup.py`) | `<cache_dir>/statcan_17100009.csv` |
| Statistics Canada table 17-10-0057 | Provincial population projections, M1 medium-growth scenario | `statcan.load_population_projections` (via `setup.py`) | `<cache_dir>/statcan_17100057.csv` |
| Statistics Canada table 38-10-0048 | Relative usage shares of energy-saving light bulb types, by province | `statcan.get_statcan_table` (module-level in `lighting.py`) | `<cache_dir>/statcan_38100048.csv` |
| NREL ResStock (residential building stock energy models) | Hourly end-use energy consumption by housing archetype, by state | `nrcan.get_data` (via `all_subsectors.py`, `aggregate_dsd`) | `<cache_dir>/<url-derived filename>.csv` |
| EPA GHG Emission Factors Hub | CO2/CH4/N2O emission factors by fuel | `nrcan.get_data` (via `all_subsectors.py`, emissions step) | `<cache_dir>/ghg-emission-factors-hub-2025.csv` |
| Renewables Ninja API | Hourly historical temperature/humidity for US states and Canadian provinces, used to build weather-based demand profiles | `weather_mapping.get_weather_data` | `<cache_dir>/<url-derived filename>_<weather_year>.csv` |

## Notes

- `nrcan.get_data` is a generic CSV/Excel/XML downloader with local caching;
  it backs both the NRCan Handbook fetch and the ResStock/EPA fetches above
  since none of those need NRCan-specific URL templating.
- ResStock and the EPA emission factors hub are each fetched once per
  aggregation run regardless of region count; NRCan COMPR-DB tables and
  Renewables Ninja weather data are fetched once per region (and cached
  thereafter).
- Renewables Ninja requires a personal API token in
  `input_files/rninja_api_token.txt` (gitignored); requests without a valid
  token raise `ValueError` in `weather_mapping.get_weather_data`.