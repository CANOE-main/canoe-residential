# -*- coding: utf-8 -*-
from __future__ import annotations

from sqlite3 import Cursor

import pandas as pd
from loguru import logger

from canoe_schema.v4_0.models import Efficiency, LifetimeTech, Technology

from canoe_residential.common import ResidentialRuntime


def add_electricity_bridge(
    module_config: ResidentialRuntime,
    db_cursor: Cursor,
) -> None:
    """Add the electricity-to-residential transfer pathway.

    Creates:
        E_elc_dem -> E_R_ELC -> R_elc
    """
    ids = {
        "CAN": f"RESHR{module_config.cfg.version}",
        **{
            region.name: f"RESHR{region.name}{module_config.cfg.version}"
            for _, region in module_config.regions.iterrows()
        }
    }

    sector = "R"
    transfer_tech = f"E_{sector}_ELC"
    input_comm = "E_elc_dem"
    output_comm = f"{sector}_elc"

    configured_fuels = {fuel.name.lower() for _, fuel in module_config.fuel_commodities.iterrows()}
    if "electricity" not in configured_fuels:
        logger.warning(
            "Electricity bridge skipped because 'electricity' is not configured in fuel_commodities."
        )
        return

    # Technology is global/national in the sector dataset, matching the way
    # the rest of the residential technology scaffolding is registered.
    tech_row = Technology(
        tech=transfer_tech,
        flag="p",
        sector="residential",
        unlim_cap=1,
        annual=0,
        description="Electricity transfer from the electricity sector to residential",
        data_id=ids["CAN"],
    )
    db_cursor.executemany(*Technology.bulk_insert_or_ignore_sql([tech_row]))

    efficiency_rows: list[Efficiency] = []
    lifetime_rows: list[LifetimeTech] = []

    for _, region in module_config.regions.iterrows():
        region = region.name
        if region == "CAN":
            continue

        data_id = ids[region]

        for vintage in module_config.cfg.future_periods:
            efficiency_rows.append(
                Efficiency(
                    region=region,
                    input_comm=input_comm,
                    tech=transfer_tech,
                    vintage=vintage,
                    output_comm=output_comm,
                    efficiency=1.0,
                    notes="Arbitrary value for electricity transfer technology",
                    data_id=data_id,
                )
            )

        lifetime_rows.append(
            LifetimeTech(
                region=region,
                tech=transfer_tech,
                lifetime=5,
                notes=(
                    "Arbitrary lifetime so the electricity transfer technology "
                    "is renewed as often as needed"
                ),
                data_id=data_id,
            )
        )

    if efficiency_rows:
        db_cursor.executemany(
            *Efficiency.bulk_insert_or_ignore_sql(efficiency_rows)
        )

    if lifetime_rows:
        db_cursor.executemany(
            *LifetimeTech.bulk_insert_or_ignore_sql(lifetime_rows)
        )

    logger.info(
        "Electricity bridge {}: Technology=1, Efficiency={}, LifetimeTech={}",
        transfer_tech,
        len(efficiency_rows),
        len(lifetime_rows),
    )
