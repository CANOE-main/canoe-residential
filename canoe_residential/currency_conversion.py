"""
Currency conversion utilities for canoe-residential.

conv_curr() converts a cost from its original currency/year to the
final base currency/year configured in params.yaml.
"""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from canoe_residential.common import ResidentialRuntime


def conv_curr(
    runtime: ResidentialRuntime,
    orig_cost: float,
    orig_year: int | None = None,
    orig_curr: str | None = None,
) -> float:
    """
    Convert a cost from its original currency/year to the base currency/year.

    Args:
        runtime:   populated ResidentialRuntime (for config + exchange tables).
        orig_cost: the original cost value from the data source.
        orig_year: the data source's currency year; defaults to cfg.aeo_currency_year.
        orig_curr: the data source's currency code (USD, CAD, etc.);
                   defaults to cfg.aeo_currency.

    Example:
        cost_cad2020 = conv_curr(runtime, 2500, 2010, 'USD')
    """
    if orig_year is None:
        orig_year = runtime.cfg.aeo_currency_year
    if orig_curr is None:
        orig_curr = runtime.cfg.aeo_currency

    cfg = runtime.cfg
    exchange = runtime.currency_exchange
    inflation = runtime.currency_inflation

    base_fact = (
        exchange.loc[cfg.final_currency_year, cfg.final_currency]
        * inflation.loc[cfg.final_currency_year, cfg.inflation_index]
    )
    return (
        orig_cost
        * exchange.loc[orig_year, orig_curr]
        * inflation.loc[orig_year, cfg.inflation_index]
        / base_fact
    )


def convert_currencies(runtime: ResidentialRuntime, conn: sqlite3.Connection) -> None:
    """Apply currency conversion to all cost table rows in the database."""
    cost_tables = {
        "CostInvest": "cost_invest",
        "CostFixed": "cost_fixed",
        "CostVariable": "cost_variable",
    }
    curs = conn.cursor()
    cfg = runtime.cfg

    base_curr = cfg.final_currency
    base_year = cfg.final_currency_year

    for table, header in cost_tables.items():
        df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
        df[header] = [
            conv_curr(runtime, cost, year, curr)
            for cost, year, curr in df[[f"data_{header}", "data_cost_year", "data_curr"]].values
        ]
        df[f"{header}_units"] += f" {base_year} {base_curr}"
        curs.execute(f"DELETE FROM {table}")
        df.to_sql(table, conn, if_exists="append", index=False)

    conn.commit()
    print(f"Currencies converted to {base_year} {base_curr}.\n")


if __name__ == "__main__":
    from canoe_residential.runtime import build_runtime
    import sqlite3 as _sqlite3

    rt = build_runtime()
    with _sqlite3.connect(rt.cfg.db_dir) as _conn:
        convert_currencies(rt, _conn)
