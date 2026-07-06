# canoe-residential — Refactor Decision Log

Decisions recorded during the Stage 2 refactor (see `design_docs/CANOE_module_refactor_design.md`,
Section 13 template).

---

### `Commodity` — CO2-equivalent emission commodity

- **Question:** Should the `CO2eq` emission commodity row be owned by canoe-residential, or
  should `canoe-base` seed a global emission commodity that all sector modules validate against?
- **Decision:** `canoe-residential` writes the row with `INSERT OR IGNORE`. If canoe-base or
  another module seeds it first (with the same `name`), the insert is silently skipped. If no
  other module has seeded it, canoe-residential introduces it.
- **Owner/rationale:** Decided during Step 3 (Yamil, 2026-06-17). Using `INSERT OR IGNORE`
  avoids ownership conflicts now without requiring a canoe-base change. A module that owns the
  row more cleanly (e.g. a future `canoe-policy` or canoe-base) can take it over without code
  changes here.
- **Follow-ups:** When canoe-base gains a global emission commodity, confirm the `name` matches
  `params['emission_commodity']` in `input_files/params.yaml`, then remove the
  `Commodity(name=..., flag='e', ...)` write from `all_subsectors.pre_process()` and validate
  instead.

---

### `SeasonLabel`, `TimeOfDay`, `TimeSeason`, `TimeSegmentFraction` — time-slice seeding

- **Question:** Should canoe-residential write the time-slice tables, or validate that canoe-base
  has already seeded them?
- **Decision:** `canoe-residential` writes them with `INSERT OR IGNORE` (idempotent). The
  validation step (`validation._check_time_slices`) runs first and emits a warning if the
  entries are absent, surfacing the fact that canoe-base did not pre-populate them.
- **Owner/rationale:** Decided during Step 3 (Yamil, 2026-06-17). Canoe-base does not yet seed
  these tables. Using `INSERT OR IGNORE` keeps re-runs safe and avoids a blocking dependency on
  a canoe-base change.
- **Follow-ups:** Once canoe-base seeds `SeasonLabel`, `TimeOfDay`, `TimeSeason`, and
  `TimeSegmentFraction`, change the writes in `all_subsectors.pre_process()` to validation-only
  (remove the `INSERT OR IGNORE` loops, harden `_check_time_slices` to raise on missing entries).

---

### `TimePeriod` (future periods) and `Region` — B-category writes removed

- **Question:** These were written by `pre_process()` with `REPLACE INTO`, which could clobber
  canoe-base's rows.
- **Decision:** Writes removed entirely. `validation._check_periods` and `validation._check_regions`
  verify they exist in the DB (with the correct flag for periods) before any sector writes.
  Failure raises `ValueError` (or warns) per `params.get('validation_behavior', 'error')`.
- **Owner/rationale:** Decided during Step 3 (Yamil, 2026-06-17). These are unambiguously
  B-category tables (design doc §6.1); canoe-base must seed them.
- **Follow-ups:** Ensure canoe-base seeds all periods in `model_periods` + the boundary period
  (`model_periods[-1] + period_step`) with `flag='f'`, and all regions in `regions.csv` (where
  `include=True`) with matching `region` keys.