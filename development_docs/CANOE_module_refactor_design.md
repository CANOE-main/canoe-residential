# CANOE Data Module Refactoring — Design Document

**Audience:** anyone (human or agent) refactoring a `canoe-*` sector data module.
**Status:** living document — the table-classification appendix and module notes
are starting points, not final answers. Several questions are explicitly left
for per-module review (see "Decisions to make per module").

---

## 1. Reference materials

- Schema package (Pydantic models + DDL): https://github.com/CANOE-main/canoe-schema
- **Target schema for this round of refactors is v4.0**, currently on branch
  `yep/v4` (https://github.com/CANOE-main/canoe-schema/tree/yep/v4), not yet on
  `main`. `canoe-agriculture`'s `dev` branch is already pinned to this branch
  (see its `pyproject.toml`). Merging `yep/v4` into `main` is a prerequisite
  housekeeping step, but each module can be developed against the branch in the
  meantime.
- Worked example / reference PR: https://github.com/CANOE-main/canoe-agriculture/pull/3
  ("Stage 2 refactoring and v4 schema", `dev` branch of
  https://github.com/CANOE-main/canoe-agriculture)
- TEMOA mathematical formulation (provided as PDF, also at
  https://docs.temoaproject.org/en/latest/mathematical_formulation.html) — use
  this to understand what each schema table *means* to the downstream
  optimization model. This is essential for the global/module-specific
  classification work in Section 5.
- Modules in scope for this pass: `canoe-industry`, `canoe-residential`,
  `canoe-commercial`, `canoe-electricity`, `canoe-fuel`.
- `canoe-base` (private) is **out of scope for code changes**. It creates the
  database from `canoe-schema`'s DDL and seeds the genuinely global tables
  (`time_period`, `time_of_day`, `time_season`, `time_season_sequential`,
  `region`, and a few others). It will be extended manually, table by table, as
  this refactor surfaces what each module actually needs. Sector modules treat
  it as a fixed contract: open the database it produced and append to it.

---

## 2. Why we're doing this

Today, each `canoe-*` module is a standalone script that (a) scrapes some
external sources, (b) does sector-specific transformation/allocation logic,
and (c) builds a SQLite database **from scratch**, including tables that have
nothing to do with the sector (time periods, regions, reference/enum tables).
SQL is often hand-written, untyped, and duplicated across modules. Hard-coded
constants (province lists, NRCan row indices, data-quality scores, schema
versions...) are scattered through the code.

The refactor has four priorities, in order of how concretely they're already
scoped:

1. **Separate raw-data acquisition from SQL-writing code**, and produce a
   short list of external sources per module.
2. **Replace hand-written SQL with Pydantic-model-driven SQL** against the
   versioned `canoe-schema` package, so every row is field-validated before it
   touches the database.
3. **Stop each module from creating "global" tables.** Each module opens a
   database that `canoe-base` already created and populated with shared
   structure, validates that its own configuration is consistent with that
   shared structure, and appends only sector-specific rows.
4. **Parametrize hard-coded values** using small, structured Pydantic
   models/config — following the `CANOEInputFuel` pattern already established
   in `canoe-agriculture`.

`canoe-agriculture`'s PR #3 is the only module where this has been attempted
end-to-end. It is the reference implementation for "what good looks like," but
it is **not** a finished template to copy mechanically — every module differs
enough that each of the four priorities needs to be re-applied with judgment.
Section 6 walks through what changed in agriculture and what's still rough.
Section 8 gives a process checklist; Section 9 gives quick first impressions of
the other five modules so the checklist can be tailored.

---

## 3. Target shape of a module

Not every module needs to be reorganized into exactly this file layout —
`canoe-residential`/`canoe-commercial`/`canoe-electricity` in particular have
their own multi-subsector structure that may be worth preserving (see Section
9). But every module should end up with a clear separation along these lines,
regardless of how the files are named or grouped:

```
configuration         -- Pydantic config model(s), loaded from TOML/YAML.
                          Structured sub-models for repeated "shapes"
                          (CANOEInputFuel-style), not bare dicts/lists.

data acquisition       -- Functions that talk to external APIs/files and
                          return plain Python/pandas objects (no SQL,
                          no schema imports). Caching lives here.

domain transformation   -- Sector-specific calculations (GDP scaling,
                          allocation shares, unit conversions, etc.)
                          operating on plain data, independent of SQL.

schema-row construction -- Turns transformed data into canoe_schema
                          Pydantic model instances (Demand, Efficiency,
                          Technology, ...). This is where field
                          validation happens.

persistence             -- Opens the existing SQLite DB (created by
                          canoe-base), runs validation against global
                          tables, and executes the *_sql() methods from
                          canoe_schema's CanoeBaseModel for each row
                          batch (INSERT OR IGNORE preferred, for
                          idempotency).

validation              -- Checks that the module's configuration
                          (periods, regions, ...) is consistent with
                          what's already in the database's global
                          tables. Runs before any writes.

orchestration (main)    -- Wires the above together in a clear,
                          numbered sequence. No business logic here.
```

The acquisition/transformation/row-construction/persistence boundaries don't
have to be four separate files — but they should be separable: it should be
possible to unit-test "given this transformed data, do we produce valid
`Demand` rows" without hitting the network, and to swap out a data source
without touching SQL.

---

## 4. Workstream 1 — Separate data acquisition from persistence

**Goal:** every module should have one or more clearly named modules
(`data_scraper.py`, `statcan.py`, `coders_api.py`, `eia_api.py`, ...) whose
public functions:

- take simple inputs (years, region codes, cache directory),
- return plain `pandas.DataFrame` / `dict` / `list` objects,
- contain **all** network calls, file parsing, and caching,
- import **nothing** from `canoe_schema` and write **no SQL**.

`canoe-agriculture`'s `data_scraper.py` (NRCan CEUD + CER macro indicators) and
`statcan.py` (StatCan 25-10-0029-01 Atlantic shares) already fit this pattern
well and can be used as the template. Each function should be documented (even
just a docstring) with:

- which external source it hits (name + base URL),
- what it returns (shape of the DataFrame/dict),
- caching behavior and cache file location.

### Deliverable: external sources list

For each module, produce a short table (in the module's README or a new
`SOURCES.md`) — **for human reference only**, not a machine-readable manifest
— listing:

| Source | What it provides | Accessed by | Cache file |
|---|---|---|---|
| e.g. NRCan CEUD | Sector energy use by province/fuel | `data_scraper.load_cached_or_fetch_agri` | `cache/dataframes.pkl` |

This is also a good time to flag sources that are fetched but barely used, or
sources duplicated across modules (e.g. if `canoe-electricity` and
`canoe-fuel` both hit similar EIA/StatCan endpoints — worth knowing, even if we
don't consolidate them now).

---

## 5. Workstream 2 — Pydantic-model-driven SQL

**Goal:** every `INSERT`/`UPSERT`/`UPDATE`/`DELETE` against the database goes
through a `canoe_schema.v4_0.models.<Table>` instance and one of the
`CanoeBaseModel` SQL-builder methods, never a hand-written SQL string.

`CanoeBaseModel` (in `canoe_schema/base.py`) provides, for any model instance
or list of instances:

- `to_insert_sql()` / `to_bulk_insert_sql()`
- `to_insert_or_ignore_sql()` / `bulk_insert_or_ignore_sql()` — **preferred**
  for idempotent re-runs
- `to_upsert_sql()` (ON CONFLICT ... DO UPDATE)
- `to_replace_sql()` / `bulk_replace_into_sql()`
- `to_update_sql()` / `to_delete_sql()` (by primary key)

All models use `model_config = ConfigDict(extra="forbid")`, and fields are
typed (including enums like `CommodityTypeCode`, `OperatorCode`,
`DataQualityCredibilityLevel`, etc., from `canoe_schema.v4_0.enums`). This
means constructing a row with a typo'd field name, a missing required field,
or an out-of-range value (e.g. `dq_cred=7`, or `factor=1.5` where the schema
constrains `0 <= factor <= 1`) fails at construction time, not at `INSERT`
time — which is the "field validation against a versioned schema" the project
wants.

### Mechanical pattern (from `canoe-agriculture`)

```python
from canoe_schema.v4_0.models import Demand

rows = [Demand(region=..., period=..., commodity=..., demand=..., ...) for ...]
db_cursor.executemany(*Demand.bulk_insert_or_ignore_sql(rows))
```

### What to do per module

1. Find every place SQL is built as a string (`f"INSERT INTO ..."`,
   `df.to_sql(...)`, raw `cursor.execute("INSERT ...")`) and replace it with
   the construction of `canoe_schema.v4_0.models` instances + a `*_sql()` call.
2. Where the module currently builds a `pandas.DataFrame` and writes it with
   `to_sql(..., if_exists="append")` at the end of the run (the "comb_dict"
   pattern from `canoe-agriculture`'s `main` branch and from `canoe-fuel`),
   prefer converting that step to per-row Pydantic construction +
   `bulk_insert_or_ignore_sql`. If a module's logic is genuinely easier to
   express as DataFrame operations, it's fine to keep the DataFrame for
   *transformation*, but the final write should still go through the Pydantic
   models (e.g. `[Model(**row) for row in df.to_dict("records")]`).
3. Map each table the module writes to its v4.0 model. **Field names and
   shapes have changed since v3.1/v3.2** (e.g. column renames, new
   data-quality fields `dq_cred`/`dq_geog`/`dq_struc`/`dq_tech`/`dq_time`,
   `data_id` provenance keys). Expect every module to need some amount of
   "minor changes to match v4" as the user described — this is normal and
   should be done table-by-table, checking the model's field list against
   what the module currently produces.
4. Use the schema enums (`CommodityTypeCode`, `TechnologyTypeCode`,
   `OperatorCode`, `TimePeriodTypeCode`, `DataQuality*Level`) instead of raw
   strings/ints where the model field is typed as an enum.

---

## 6. Workstream 3 — Global vs. module-specific tables

This is the highest-value, highest-judgment workstream. The core idea:

> A module should never **define** rows in tables that represent shared model
> structure (time, regions, and small reference/enum tables). It should
> **validate** that its own configuration is compatible with what's already
> there, and fail loudly (or warn, per config) if not.

### 6.1 Three categories of tables

Going through the v4.0 schema, every table falls into roughly one of three
buckets:

**(A) Schema-seeded constants.** Populated by `schema.sql` itself via
`REPLACE INTO` when the database is created — no module action needed, ever.
These are: `commodity_type`, `technology_type`, `time_period_type`, `operator`,
and the five `data_quality_*` tables, plus `metadata`/`metadata_real` defaults
(`DB_MAJOR`/`DB_MINOR`, `global_discount_rate`, `default_loan_rate`). A module
might *read* these (e.g. to validate an operator code is one of `e`/`le`/`ge`),
but the Pydantic enums already encode them, so usually not even that.

**(B) Global structure tables (canoe-base's job).** Define the shared
time/region/sector skeleton that every module's rows are indexed against:
`time_period`, `time_of_day`, `time_season`, `time_season_sequential`,
`region`. A module **reads** these to validate its config (see 6.2) and
**never writes** to them.

**(C) Module-specific content tables.** Everything else — `technology`,
`commodity`, `demand`, `existing_capacity`, `efficiency`, all the `cost_*` and
`limit_*` tables, `data_set`, `data_source`, etc. A module **writes** to these,
scoped to its own technologies/commodities/data IDs, using `INSERT OR IGNORE`
for idempotency.

The appendix in Section 10 walks the full v4.0 table list with a tentative
(A)/(B)/(C) classification and TEMOA-formulation cross-references, **plus a
fourth flag, (?), for tables that don't cleanly fit** — see 6.3.

### 6.2 Validation pattern (the "stage 2" contract)

`canoe-agriculture`'s `validation.py` is the template:

```python
def validate_db_against_config(module_config, db_conn):
    missing_periods = check_missing_periods(
        db_conn, module_config.existing_periods, module_config.future_periods
    )
    if missing_periods:
        if module_config.validation_behavior == "error":
            raise ValueError(...)
        logger.warning(...)
```

It checks that every period the module is configured to produce data for
already exists in `time_period` **with the correct `flag` (`e` for existing,
`f` for future)**. This runs *before* any writes (`main.py` calls it as step
0).

Generalize this pattern for every category-(B) table a module's config
touches:

- **Periods** → `time_period` (existing pattern).
- **Regions** → `region`. If `module_config.province_list` (or equivalent)
  contains a region not present in `region`, that's an error/warning depending
  on `validation_behavior`.
- **Time slices** (`time_season`, `time_of_day`, `time_season_sequential`) →
  only relevant for modules that produce time-slice-resolved data (e.g.
  `capacity_factor_process`, `demand_specific_distribution`,
  `limit_tech_*_split`). If a module writes rows keyed by `(season, tod)`, it
  should validate that those `(season, tod)` pairs exist in `time_season` /
  `time_of_day` rather than assuming a fixed 365×24 grid (the old
  `add_time_agri` hard-coded `D001`..`D365` × `H01`..`H24`, which is exactly
  the kind of thing canoe-base now owns and modules must not re-derive).

Keep validation **read-only and config-driven**, not "pull a global config
object and trust it." The user has explicitly asked to lean on validation
against the live database rather than threading a shared config object through
every module right now (see Section 11 — shared config package is deferred).

### 6.3 Decisions to make per module ("(?)" tables)

Some tables are ambiguous because the *thing they describe* can legitimately
span sectors, even though the row itself will be written by one module. These
need a decision during that module's refactor — don't guess silently; record
the decision (who decided, when, why) in the module's own docs. Recurring
patterns to watch for:

- **`tech_group` / `tech_group_member` / `tech_group_label`.** A "tech group"
  like *all electricity generation* or *all renewable technologies* may need
  members from multiple sector modules. If a group is genuinely
  cross-sector, who owns the `tech_group` row and how do other modules safely
  add members to it via `INSERT OR IGNORE` without clobbering each other?
- **Shared commodities.** `canoe-agriculture` namespaces its commodities with
  a sector prefix (`A_elc`, `A_ng`, ...) specifically to avoid collisions, but
  many sectors genuinely consume the *same* underlying energy carrier
  (electricity, natural gas, diesel...) and may need to trade it through
  `commodity_balance` at the system level. Is the convention "every sector
  defines its own prefixed copy of each fuel commodity" the long-term answer,
  or should some commodities be shared/global? (Likely depends on whether
  `canoe-fuel` is meant to be the "supply side" that other sectors' input
  commodities connect to — worth checking when refactoring `canoe-fuel`.)
- **Emission commodities & `cost_emission`.** Emission commodities (e.g. CO2)
  and economy-wide carbon pricing (`cost_emission`, `limit_emission`) look
  like cross-sector policy parameters rather than something any single sector
  module should "own." Each module will still write `emission_activity` rows
  for its own technologies, but the emission *commodity* definition and any
  emission *cost/limit* may belong elsewhere (canoe-base, or a future
  cross-cutting "policy" module).
- **`planning_reserve_margin`, `rps_requirement` (deprecated —
  `limit_activity_share` is its replacement per the schema docs), and any
  `limit_*_share` table comparing two `tech_group`s.** These often encode
  policy targets that reference technologies from multiple sectors.
- **`commodity_label` / `technology_label` / `data_source_label`
  registries.** These look like de-duplicated, data_id-independent name
  registries (per the v3.1→v3.2 migration notes: detecting tech names
  duplicated across datasets). Default recommendation: each module
  `INSERT OR IGNORE`s a label row whenever it introduces a new
  technology/commodity/data-source code, using its own namespacing
  convention to avoid collisions — but confirm this doesn't conflict with
  whatever canoe-base ends up seeding for these tables.

When in doubt: **write the row only if the module is the natural owner of the
underlying real-world thing**, validate against everything else, and write
down the open question rather than resolving it unilaterally.

---

## 7. Workstream 4 — Parametrization & structured config

**Goal:** hard-coded lists/dicts/magic numbers become fields on a Pydantic
config model, with structured sub-models for anything that's a "list of
records" rather than a "list of scalars."

`canoe-agriculture`'s `common.py` is the template:

```python
class CANOEInputFuel(BaseModel):
    shortname: str
    longname: str
    nrcan_row_idx: int

class CANOEAgricultureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "3.1"
    db_dir: str
    existing_periods: list[int]
    future_periods: list[int]
    province_list: list[str]
    validation_behavior: Literal["error", "warning"] = "error"
    version: str = "001"
    input_fuels: list[CANOEInputFuel]
    remainder_fuel_limit_tech_annual: str

    @classmethod
    def validate_from_toml(cls, toml_dir: str) -> "CANOEAgricultureConfig":
        ...
```

Apply this pattern per module, prioritizing roughly in this order (highest
value first, but — per the user's note — the actual priority list will depend
on what's found in each module, so treat this as a starting checklist, not a
mandate):

1. **Region/province lists and any per-region mappings** (e.g.
   `canoe-agriculture`'s `ATL_MAP` from province codes to StatCan region
   names, `atl_pro`). These are exactly the kind of "list of records" that
   wants a small struct (e.g. `region_code`, `external_geo_name`,
   `is_aggregate_member_of`).
2. **External-source-specific row/column indices and identifiers** (NRCan
   `nrcan_row_idx`, EIA series IDs, StatCan table/column names) — already
   partly done for agriculture via `CANOEInputFuel`; look for similar
   "magic index into an external table" patterns in other modules
   (`canoe-electricity`'s `coders_api.py`, `canoe-fuel`'s `eia_api.py`).
3. **Scenario/series selectors** for macro data (e.g. agriculture hard-codes
   `Scenario == "Global Net-zero"` and
   `Variable == "Real Gross Domestic Product ($2012 Millions)"` inside
   `_gdp_scalers`). These should be config fields, not string literals buried
   in a function.
4. **Data-quality score tuples.** Agriculture currently hard-codes
   `dq_cred=1, dq_geog=1, dq_struc=2, dq_tech=3, dq_time=2` per table/section.
   Consider a small `DataQualityProfile` struct that's set once per
   data-construction step and spread into each row, so the *justification*
   for a given score lives in one place (ideally with a comment explaining
   the reasoning) rather than being repeated at every call site.
5. **`DataSource`/`DataSourceLabel` reference rows** (the `A1`–`A4` style
   blocks in `post_processing.py`). These are currently literal lists of
   `[source_id, source, notes, data_id]` tuples. At minimum, turn each into a
   `DataSource(...)` Pydantic construction (Workstream 2); whether to also
   move the *content* into config is a judgment call — these read more like
   "data" than "parameters," so a plain list of `DataSource` instances defined
   near the top of `post_processing.py` (or a small `sources.py`) is probably
   fine, as long as it's not raw SQL.
6. **Sector identity constants** (`sector_initial`, `sector_abv`,
   `sector_longname`, the `A_`/`AGRIHR` ID-prefix conventions) — already mostly
   parametrized in agriculture's config; replicate for other sectors.

Don't force every literal into config — the goal is removing things that are
*genuinely* configuration (would plausibly change between runs/scenarios) or
that encode *domain knowledge that should be reviewable in one place* (data
quality justifications, source row indices). A loop variable or a column name
that's intrinsic to the table schema doesn't need to become a config field.

---

## 8. Reference walkthrough: `canoe-agriculture` PR #3

What changed, file by file (comparing `main` → `dev`):

- **`common.py`**: `Config` (loose `pydantic.BaseModel`, few fields) →
  `CANOEAgricultureConfig` (`extra="forbid"`, many more fields, TOML loader,
  `CANOEInputFuel` struct). This is the Workstream 4 template.
- **`setup.py`**: `load_runtime_agri` used to call `prepare_database()` (drop +
  recreate the SQLite file from `schema.sql`) and `create_empty_comb_dict`. In
  `dev`, `prepare_database` is no longer called — the DB at `cfg.db_dir` is
  assumed to already exist (built by canoe-base) and `retrieve_tables_from_db`
  / `create_empty_comb_dict` just introspect it. **This is the literal
  Workstream 3 change**: stop creating the DB, start consuming one.
- **`validation.py`** (new file): `validate_db_against_config` +
  `check_missing_periods`, run as step 0 of `main.py`. This is the Workstream
  3 validation contract described in 6.2.
- **`techcom.py`**: previously built a `Technology`/`Commodity` DataFrame for
  later `to_sql`; now constructs `canoe_schema.v4_0.models.Technology` /
  `Commodity` instances and writes via `*_sql()` methods directly inside the
  function (Workstream 2). Also now driven by `module_config.input_fuels`
  (`list[CANOEInputFuel]`) instead of hard-coded `commodity_list` /
  `commodity_list_ex` parallel lists (Workstream 4).
- **`demands.py`**: same DataFrame → Pydantic-row conversion for `Demand` and
  `ExistingCapacity`. Logic (GDP scaling, ATL share allocation) is unchanged —
  this is "transformation" code that was already reasonably separated from
  persistence; only the persistence step changed.
- **`techinput.py`**: `LimitTechInputSplitAnnual` + `Efficiency` rows now
  Pydantic-constructed; the "remainder fuel" is now
  `module_config.remainder_fuel_limit_tech_annual` with a validation check
  that it's actually in `input_fuels` (Workstream 4).
- **`post_processing.py`**: `DataSet`/`DataSource` rows now Pydantic-constructed
  and written via `bulk_insert_or_ignore_sql`. The `DataSource` reference list
  itself (`A1`-`A4`) is still a literal list of tuples — see Workstream 4,
  item 5, for the recommended treatment.
- **`data_scraper.py` / `statcan.py`**: essentially unchanged — these were
  already "pure data acquisition" and serve as the Workstream 1 template.
- **`efficiency.py`** (main branch) was folded into `techinput.py` in `dev`.
- **`costs.py`** (`build_cost_invest_agri`): **not yet migrated** — still
  builds a DataFrame with `np.nan` placeholders, marked
  `TODO This needs refactoring before it can be used`, and is commented out in
  `main.py`. This is a good first task for whoever picks up agriculture again,
  and a reminder that **PR #3 is "Stage 1/2 in progress," not "done."**

### Remaining open items visible in `canoe-agriculture` itself

- `costs.py` / `CostInvest` not migrated (above).
- `main.py` comment `# TODO replace with CANOEProvince class` for
  `province_list: list[str]` — a candidate for the future shared package
  (Section 11), but for now just a `list[str]` validated against `region`.
- The `agriculture.md` / `TODO.md` files have a few stale items (README
  references an `aggregator_agri.py` that doesn't exist under that name
  anymore; `TODO.md` references "Parametrize the hard-coded values in
  setup.py" — mostly done, but `setup.py` still hard-codes
  `sector_abv`, `province_list`, `atl_pro`, `commodity_list*` inside
  `load_runtime_agri`'s `__domain__` dict, which overlaps with config fields
  that already exist on `CANOEAgricultureConfig`. Worth de-duplicating: either
  the `__domain__` dict or the config fields should be the single source of
  truth, not both.

---

## 9. Per-module process checklist

For each of `canoe-industry`, `canoe-residential`, `canoe-commercial`,
`canoe-electricity`, `canoe-fuel`, run this process. Steps are ordered but not
strictly sequential — Workstream 3 findings often inform Workstream 4 config
fields, for example.

**Step 0 — Inventory.**
- List every file, and for each one, classify it as: data acquisition,
  transformation, schema-row construction / persistence, orchestration, or
  "other/legacy" (dead code, notebooks, etc.).
- List every table the module currently writes to (grep for table names /
  `to_sql` / `INSERT`).
- List every external source it hits (grep for `requests`, `urllib`,
  hard-coded URLs).
- Identify the current DB-creation step (does it call something like
  `instantiate_database()` / `prepare_database()` that drops and recreates the
  whole DB from `schema.sql`?). This is the Workstream 3 entry point.

**Step 1 — Data acquisition separation (Workstream 1).**
- Ensure acquisition functions are import-clean of `canoe_schema` and SQL.
- Write the `SOURCES.md` table.

**Step 2 — Stop creating the database (Workstream 3, mechanical part).**
- Replace "create DB from schema.sql" with "open the existing DB at the
  configured path."
- Decide what `force_wipe_database`-style behavior (seen in
  `canoe-residential`/`canoe-commercial`/`canoe-electricity`'s
  `instantiate_database`) should become — likely just removed, since wiping
  the shared DB would destroy canoe-base's and other modules' data. If a
  "wipe my module's own rows for a clean re-run" behavior is wanted, that's a
  `DELETE WHERE data_id IN (...)` against the module's own `data_id`s, not a
  full-DB wipe.

**Step 3 — Validation (Workstream 3, judgment part).**
- Write/port a `validate_db_against_config`-equivalent: check periods against
  `time_period`, regions against `region`, and any time-slice assumptions
  against `time_season`/`time_of_day`.
- Walk the list of tables from Step 0 against the appendix in Section 10.
  For each `(?)`-flagged table the module touches, record a decision (see
  6.3) — even if the decision is "defer."

**Step 4 — Pydantic SQL (Workstream 2).**
- For each table from Step 0, find the matching `canoe_schema.v4_0.models`
  class, reconcile field names/types against what the module currently
  produces, and replace the write path with `*_sql()` calls (prefer
  `bulk_insert_or_ignore_sql`).

**Step 5 — Parametrization (Workstream 4).**
- Build/extend the module's Pydantic config model. Apply the priority list in
  Section 7, adapted to what's actually hard-coded in this module.

**Step 6 — Wire it up.**
- Update the orchestration entry point (`main`/`__main__`/`aggregator`) to:
  validate → acquire data → transform → construct rows → write. Keep this file
  free of business logic.
- Update the module's README to reflect the new flow and link `SOURCES.md`.

---

## 10. First-pass notes per module

These are based on a directory/file-level scan only (not a full read), to help
scope Step 0 for each module. Treat all of this as hypotheses to confirm.

### `canoe-industry`
Structurally the closest analog to `canoe-agriculture`'s pre-PR-3 state:
`aggregator.py`, `common.py`, `costs.py`, `data_scraper.py`, `demands.py`,
`efficiency.py`, `post_processing.py`, `setup.py`, `statcan.py`, `techcom.py`,
`techinput.py` — same author, same shape, including a `setup.py` with its own
`Config` class (plain `__init__(self, params: dict)` + properties, not
Pydantic) and `prepare_database()`. Likely the **second-easiest module** to
bring in line, largely by following the agriculture PR #3 pattern directly,
including the v3.1/v3.2 → v4.0 model field reconciliation. Its `Config` class
should become a `CANOEAgricultureConfig`-style Pydantic model.

### `canoe-fuel`
Smaller, similar lineage (`aggregator.py`, `setup.py`, `techcom.py`,
`efficiency.py`, `costvariable.py`, `emissionactivity.py`, `postprocessing.py`,
`eia_api.py`). `setup.py` has its own `init_database()` that recreates the DB
from `schema.sql` and a YAML-based config loader (`load_config`) returning a
raw `dict` rather than a Pydantic model. Given the cross-module commodity
question raised in 6.3 (is `canoe-fuel` the "supply side" for shared fuel
commodities?), this module's `techcom.py`/commodity definitions deserve extra
attention during Step 3 — flag this explicitly when refactoring it, since the
answer may affect the commodity-namespacing convention recommended to other
modules too.

### `canoe-residential`, `canoe-commercial`
These two appear to share a common author/template ("Ian David Elder"):
`setup.py` defines `instantiate_database()` (build-or-wipe-and-rebuild the DB
from `config.schema_file`), plus `reference`/`bibliography` classes for
building `DataSource`/`DataSourceLabel`-style citation lists — a hand-rolled
precursor to Workstream 2/4 for that one table. Both are organized by
**subsector**: `canoe-residential` has `appliances.py`, `lighting.py`,
`space_cooling.py`, `space_heating.py`, `water_heating.py`,
`residential_sector.py`, `all_subsectors.py`, plus `weather_mapping.py`,
`currency_conversion.py`, `model_reduction.py`; `canoe-commercial` mirrors this
with `existing_capacity.py`, `new_capacity.py`, `comstock_dsd.py`,
`commercial_sector.py`, `all_subsectors.py`, plus the same
`weather_mapping.py`/`currency_conversion.py`/`utils.py`.

Two things to watch for here:
- The subsector-per-file structure is probably **worth keeping** — the target
  shape in Section 3 is about separating concerns (acquisition vs. transform
  vs. persistence), not about file count. Each subsector file likely mixes all
  of those concerns today and should be split (or at least have its
  persistence calls converted to Pydantic) without necessarily merging
  subsectors together.
- `weather_mapping.py` and `currency_conversion.py` look like they could be
  **shared utilities duplicated across modules** (residential, commercial, and
  possibly electricity all reference `currency_conversion.py` /
  `weather_mapping.py` / `utils.py` by the same names). Worth checking whether
  these are literally copy-pasted between repos — if so, that's a candidate
  for the future shared package (Section 11), even if we don't act on it now.
  At minimum, note it so it's not re-discovered three times.

### `canoe-electricity`
The most structurally distinct module. In addition to the
`reference`/`bibliography`/`instantiate_database()` pattern shared with
residential/commercial, it has a `provincial_data/` package with per-province,
per-topic files (`provincial_data/default/*.py` and `provincial_data/on/*.py`
for Ontario-specific overrides — `existing_capacity_credits.py`,
`existing_hydro_capacity_factors.py`, `existing_vre_capacity_factors.py`,
`cogen.py`, `cost_tx_dx.py`, `ramp_rates.py`). It also has
`capacity_credits.py`, `capacity_factors.py`, `generators.py`,
`new_wind_solar.py`, `constraints.py`, `interfaces.py`, `provincial_grids.py`,
`coders_api.py` (external data source), `pre_processing.py`,
`post_processing.py`, `model_reduction.py`.

This module almost certainly carries the **most `(?)`-flagged tables** from
Section 6.3: `tech_group`/`tech_group_member` (renewable/clean groupings),
`planning_reserve_margin`, `rps_requirement`/`limit_activity_share`,
`linked_tech`, and possibly `cost_emission`/emission commodity definitions all
look electricity-adjacent. The `provincial_data/default` vs `provincial_data/on`
split (a default + per-province override pattern) is also a useful existing
idea — if other modules end up needing per-region overrides, this is a
reasonable pattern to point to, rather than inventing a new one.

Given its size and the number of open policy-table questions, **plan for
`canoe-electricity` to take the most calendar time and the most back-and-forth
on Section 6.3 decisions** — it's not necessarily harder to write, but it's the
module most likely to surface cross-sector questions that need the user's
input.

---

## 11. Explicitly deferred / out of scope for this round

- **Shared `canoe-common`-style package.** Several things would clearly
  benefit from a shared home eventually: a `CANOEProvince`/region type, a base
  config model with the fields every module repeats
  (`existing_periods`/`future_periods`/`province_list`/`version`/
  `schema_version`/`db_dir`/`validation_behavior`), `check_missing_periods`-style
  validation helpers, and possibly `weather_mapping`/`currency_conversion`
  utilities (Section 9). **Not building this now** — instead, each module gets
  its own Pydantic config and its own validation functions, following the
  agriculture pattern by *duplication*. When patterns stabilize across 2-3
  modules, factoring them out becomes much easier and more obviously correct.
  Leave a `# TODO` comment (agriculture already has one for `CANOEProvince`)
  wherever a future extraction point is obvious.
- **Typed read layer.** Workstream 2 covers writes only. Reads for validation
  (Section 6.2) remain raw `cursor.execute(...)` SQL, as in
  `canoe-agriculture`'s `validation.py`. Not in scope to build a generic typed
  query layer right now.
- **`canoe-base` changes.** Out of scope for these modules' PRs. If a module's
  Step 3 surfaces a table that clearly needs to be global (B) but isn't yet
  populated by canoe-base, **document it as a request** (table name, why it's
  global, what rows it would need) rather than writing it from the sector
  module "temporarily."
- **Machine-readable source manifests.** The `SOURCES.md` deliverable
  (Section 4) is for human reference; it is not consumed by any tooling.

---

## 12. Appendix: v4.0 table classification

Classification key: **(A)** schema-seeded constant — no module action;
**(B)** global structure — module reads/validates only, canoe-base writes;
**(C)** module-specific content — module writes its own rows
(`INSERT OR IGNORE`); **(?)** ambiguous, decide per module (Section 6.3).
"TEMOA ref." gives the corresponding set/parameter symbol or name from the
mathematical formulation, where useful for understanding *why* a table is
classified the way it is.

| Table | TEMOA ref. | Class | Notes |
|---|---|---|---|
| `metadata`, `metadata_real` | `DB_MAJOR`/`DB_MINOR`, `GDR`, `DLR` | A | Seeded by `schema.sql`. |
| `commodity_type`, `technology_type`, `time_period_type`, `operator`, `data_quality_*` (×5) | enum sets | A | Seeded by `schema.sql`; mirrored as Pydantic enums. |
| `time_period` | $P_e$, $P_f$, $P^{*}$ (time_optimize) | B | Validate `existing_periods`/`future_periods` + flags against this. |
| `time_of_day` | `time_of_day`, $d$ | B | Validate any `(s,d)`-indexed rows. |
| `time_season` | `time_season`, $s$ | B | Validate any season-indexed rows. |
| `time_season_sequential` | `time_season_sequential` | B | Only relevant for `representative_periods` time-sequencing; most sector modules likely don't touch this. |
| `region` | $R$ | B | Validate `province_list`/region config against this. |
| `commodity_label`, `technology_label`, `data_source_label` | — (registries) | ? | Default: module `INSERT OR IGNORE`s its own names; confirm no conflict with canoe-base seeding. |
| `tech_group_label`, `tech_group`, `tech_group_member` | $T^G$ (tech_group), members | ? | Likely cross-sector for policy groupings (RPS, clean electricity). Decide per module, especially `canoe-electricity`. |
| `sector_label` | — | B/? | Small finite set (one row per `canoe-*` module); recommend canoe-base seeds it, modules don't write — confirm. |
| `data_set` | — | C | Each module registers its own `data_id`(s). |
| `data_source` | — | C | Each module registers its own source citations, namespaced (`A1`-`A4` style). |
| `commodity` | $C$, $C_d$, $C_e$, $C_p$, ... | C / ? | Module-specific by convention (sector-prefixed names), but cross-sector fuel/emission commodities are the recurring open question — see 6.3. |
| `technology` | $T$ and subsets ($T^b$, $T^s$, $T^a$, ...) | C | Module-specific; flags (`annual`, `flex`, `curtail`, etc.) set per the module's own technologies. |
| `capacity_credit`, `capacity_factor_process`, `capacity_factor_tech`, `capacity_to_activity` | $CC$, $CFP$, $CFT$, $C2A$ | C | Tied to the module's own `tech`/`(tech,vintage)`. |
| `construction_input`, `end_of_life_output` | $CON$, $EOLO$ | C | Module-specific. |
| `cost_fixed`, `cost_invest`, `cost_variable` | $CF$, $CI$, $CV$ | C | Module-specific. |
| `cost_emission` | $CE$ | ? | Tied to emission commodity + region/period — likely a cross-sector policy parameter (carbon price), not owned by any one sector. |
| `demand`, `demand_specific_distribution` | $DEM$, $DSD$ | C | Module-specific demand commodities. |
| `efficiency`, `efficiency_variable` | $EFF$, $EFFV$ | C | Module-specific. |
| `emission_activity`, `emission_embodied`, `emission_end_of_life` | $EAC$, $EE$, $EEOL$ | C / ? | The *rows* (tying a module's tech to an emission commodity) are module-specific; the emission *commodity itself* (e.g. `CO2`) may need to be globally defined — see 6.3. |
| `existing_capacity` | $ECAP$, $T^{ex}$ | C | Module-specific. |
| `loan_lifetime_process`, `loan_rate`, `lifetime_process`, `lifetime_tech`, `lifetime_survival_curve`, `storage_duration` | $LLP$, $LR$, $LTP$, $LTT$, $LSC$, $SD$ | C | Module-specific, tied to the module's own technologies. |
| `ramp_up_hourly`, `ramp_down_hourly` | $RUH$, $RDH$ | C | Module-specific. |
| `reserve_capacity_derate` | $RCD$ | C / ? | Tied to the module's own reserve-contributing techs; the overall reserve *margin* (below) is more clearly cross-sector. |
| `planning_reserve_margin` | $PRM$ | ? | Region-level policy parameter (electric sector reserve margin) — likely `canoe-electricity`'s to define, but verify it's not meant to be set once globally. |
| `limit_growth_capacity`, `limit_degrowth_capacity`, `limit_growth_new_capacity`, `limit_degrowth_new_capacity`, `limit_growth_new_capacity_delta`, `limit_degrowth_new_capacity_delta`, `limit_storage_level_fraction`, `limit_activity`, `limit_annual_capacity_factor`, `limit_capacity`, `limit_new_capacity`, `limit_resource`, `limit_seasonal_capacity_factor`, `limit_tech_input_split[_annual]`, `limit_tech_output_split[_annual]` | $LGC$, $LDGC$, ..., $LA$, $LACF$, $LC$, $LNC$, $LS$, $LSCF$, $TIS$/$TISA$, $TOS$/$TOSA$ | C | All keyed by `tech_or_group` within the module's own namespace — module-specific *unless* the `tech_or_group` value refers to a cross-sector group (see `tech_group` above). |
| `limit_activity_share`, `limit_capacity_share`, `limit_new_capacity_share`, `limit_emission`, `rps_requirement` | $LAS$, $LCS$, $LNCS$, $LE$ | ? | Compare two groups or cap economy-wide emissions — prime candidates for cross-sector policy. `rps_requirement` is marked deprecated in favor of `limit_activity_share`. |
| `linked_tech` | $LIT$ | ? | Power-to-gas-style links between an emissions commodity and a physical commodity — likely involves both `canoe-electricity`/`canoe-fuel`; decide ownership when refactoring those. |

---

## 13. Decision log template

Each module's refactor should end with a short "Decisions" section (in its
README or a `DECISIONS.md`) using this template for every `(?)`-flagged item
encountered:

```
### <table/topic>
- Question: <what was ambiguous>
- Decision: <what this module does now>
- Owner/rationale: <who decided, and why>
- Follow-ups: <e.g. "canoe-base needs to seed X", "revisit when canoe-electricity is refactored">
```

This keeps the per-module judgment calls visible and reviewable without
blocking on a global resolution up front.
