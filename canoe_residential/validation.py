"""
Validates module configuration against global tables already in the database.
Runs as Step 0 of build_database(), before any sector-specific writes.
"""

import sqlite3
import pandas as pd
from loguru import logger
from canoe_schema.v4_0.models import TimePeriod, Region, TimeSeason, TimeOfDay, MetadataReal

from canoe_residential.common import ResidentialRuntime


def _check_periods(
    conn: sqlite3.Connection, future_periods: list[int], validation_behavior: str
) -> None:
    cur = conn.cursor()
    placeholders = ", ".join("?" * len(future_periods))
    cur.execute(
        f"SELECT period, flag FROM {TimePeriod.__table_name__} WHERE period IN ({placeholders})",
        future_periods,
    )
    found = {row[0]: row[1] for row in cur.fetchall()}

    issues = []
    for period in future_periods:
        if period not in found:
            issues.append(f"period {period} missing from {TimePeriod.__table_name__}")
        elif found[period] != "f":
            issues.append(
                f"period {period} has flag '{found[period]}', expected 'f'"
            )

    if issues:
        msg = "Period validation failed: " + "; ".join(issues)
        if validation_behavior == "error":
            raise ValueError(msg)
        logger.warning(msg)


def _check_regions(
    conn: sqlite3.Connection, regions: list[str], validation_behavior: str
) -> None:
    cur = conn.cursor()
    placeholders = ", ".join("?" * len(regions))
    cur.execute(
        f"SELECT region FROM {Region.__table_name__} WHERE region IN ({placeholders})",
        regions,
    )
    found = {row[0] for row in cur.fetchall()}
    missing = set(regions) - found

    if missing:
        msg = f"Regions missing from {Region.__table_name__}: {sorted(missing)}"
        if validation_behavior == "error":
            raise ValueError(msg)
        logger.warning(msg)


def _check_time_slices(
    conn: sqlite3.Connection, time_df: pd.DataFrame, validation_behavior: str
) -> None:
    """Warns if seasons or times-of-day expected by the module are absent from the DB.

    Since the module INSERT OR IGNORE-s time slices (idempotent decision), missing
    entries will be seeded by this run — this check surfaces the fact that canoe-base
    did not pre-populate them.
    """
    cur = conn.cursor()

    expected_seasons = time_df["season"].unique().tolist()
    placeholders = ", ".join("?" * len(expected_seasons))
    cur.execute(
        f"SELECT season FROM {TimeSeason.__table_name__} WHERE season IN ({placeholders})",
        expected_seasons,
    )
    missing_seasons = set(expected_seasons) - {row[0] for row in cur.fetchall()}

    expected_tods = time_df["tod"].unique().tolist()
    placeholders = ", ".join("?" * len(expected_tods))
    cur.execute(
        f"SELECT tod FROM {TimeOfDay.__table_name__} WHERE tod IN ({placeholders})",
        expected_tods,
    )
    missing_tods = set(expected_tods) - {row[0] for row in cur.fetchall()}

    issues = []
    if missing_seasons:
        issues.append(f"seasons absent from {TimeSeason.__table_name__}: {sorted(missing_seasons)}")
    if missing_tods:
        issues.append(f"times-of-day absent from {TimeOfDay.__table_name__}: {sorted(missing_tods)}")

    if issues:
        # Always a warning: we INSERT OR IGNORE these, so they will be added by this run.
        logger.warning(
            "Time-slice entries not pre-seeded by canoe-base; "
            "canoe-residential will insert them: " + "; ".join(issues)
        )


def _check_discount_rate(
    conn: sqlite3.Connection, validation_behavior: str
) -> None:
    cur = conn.cursor()
    row = cur.execute(
        f"SELECT value FROM {MetadataReal.__table_name__} WHERE element = 'global_discount_rate'"
    ).fetchone()

    if row is None or row[0] is None:
        msg = (
            f"'global_discount_rate' is missing or null in {MetadataReal.__table_name__}. "
            "Set it in canoe-base before running canoe-residential."
        )
        if validation_behavior == "error":
            raise ValueError(msg)
        logger.warning(msg)


def validate_db_against_config(runtime: ResidentialRuntime, conn: sqlite3.Connection) -> None:
    """Validate the module config against global tables already in the DB.

    Raises ValueError (or warns) based on runtime.cfg.validation_behavior.
    """
    cfg = runtime.cfg
    validation_behavior = cfg.validation_behavior

    # TEMOA requires one extra boundary period beyond the last planning period.
    future_periods = [
        *cfg.future_periods,
        cfg.future_periods[-1] + cfg.period_step,
    ]

    _check_periods(conn, future_periods, validation_behavior)
    _check_regions(conn, cfg.province_list, validation_behavior)
    _check_discount_rate(conn, validation_behavior)

    if cfg.include_dsd:
        _check_time_slices(conn, runtime.time, validation_behavior)
