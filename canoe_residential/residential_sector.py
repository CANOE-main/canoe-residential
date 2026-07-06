"""
Builds residential buildings sector database
Written by Ian David Elder for the CANOE model
"""

import os
import re
import sqlite3

import canoe_residential.all_subsectors as all_subsectors
import canoe_residential.appliances as appliances
import canoe_residential.lighting as lighting
import canoe_residential.space_cooling as space_cooling
import canoe_residential.space_heating as space_heating
import canoe_residential.utils as utils
import canoe_residential.water_heating as water_heating
import canoe_residential.model_reduction as model_reduction
from canoe_residential.runtime import build_runtime
from canoe_residential.validation import validate_db_against_config
from matplotlib import pyplot as pp


def build_database():

    # 0. Load config and acquire all data (network + file I/O happens here)
    runtime = build_runtime()
    cfg = runtime.cfg

    print(f"Aggregating residential sector into {os.path.basename(cfg.db_dir)}...\n")

    if not os.path.exists(cfg.db_dir):
        raise FileNotFoundError(
            f"Database not found: {cfg.db_dir!r}. "
            "Create the database with canoe-base before running canoe-residential."
        )

    with sqlite3.connect(cfg.db_dir) as conn:

        # 1. Validate config against global tables already in the DB
        validate_db_against_config(runtime, conn)

        # 2. Pre-process: commodities, lifetimes, tech/vintage registration
        all_subsectors.pre_process(runtime, conn)

        # 3. Aggregate subsectors
        space_heating.aggregate(runtime, conn)
        space_cooling.aggregate(runtime, conn)
        water_heating.aggregate(runtime, conn)
        lighting.aggregate(runtime, conn)
        appliances.aggregate(runtime, conn)

        # 4. Cross-subsector steps
        if cfg.include_dsd:
            all_subsectors.aggregate_dsd(runtime, conn)
        if cfg.include_emissions:
            all_subsectors.aggregate_emissions(runtime, conn)
        if cfg.include_imports:
            all_subsectors.aggregate_imports(runtime, conn)

        # 5. Post-process: copy ACFs to new techs, seed existing time periods
        all_subsectors.post_process(runtime, conn)

        # 6. Write provenance: DataSource + DataSet rows, data-ID audit
        all_subsectors.write_provenance(runtime, conn)

        # 7. Cleanup: remove region-tech pairs with no capacity
        all_subsectors.cleanup(runtime, conn)

    print(f"Residential sector aggregated into {os.path.basename(cfg.db_dir)}\n")

    if cfg.simplify_model:
        model_reduction.simplify_model()
    if cfg.clone_to_xlsx:
        utils.database_converter().clone_sqlite_to_excel(
            from_sqlite_file=cfg.db_dir,
            to_excel_file=cfg.excel_output,
            excel_template_file=cfg.excel_template,
        )

    if cfg.show_plots:
        save_plots()


def save_plots(output_dir='output_plots'):
    os.makedirs(output_dir, exist_ok=True)
    print("Finished and saving plots.")
    for fig_num in pp.get_fignums():
        fig = pp.figure(fig_num)
        title = fig.get_suptitle()
        if not title and fig.axes:
            title = fig.axes[0].get_title()
        filename = title if title else f"figure_{fig_num}"
        filename = re.sub(r'[\\/:*?"<>|\x00-\x1f .,]', '_', filename)
        filepath = os.path.join(output_dir, f"{filename}.pdf")
        fig.savefig(filepath, bbox_inches='tight')
        print(f"Saved {filepath}")


if __name__ == "__main__":
    build_database()
