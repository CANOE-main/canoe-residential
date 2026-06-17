"""
Builds residential buildings sector database
Written by Ian David Elder for the CANOE model
"""

import os
import sqlite3
import re

import canoe_residential.all_subsectors as all_subsectors
import canoe_residential.utils as utils
import canoe_residential.model_reduction as model_reduction
from canoe_residential.setup import build_runtime
from canoe_residential.validation import validate_db_against_config
from matplotlib import pyplot as pp


def build_database():

    runtime = build_runtime()
    cfg = runtime.cfg

    print(f"Aggregating residential sector into {os.path.basename(cfg.db_dir)}...\n")

    # DB must already exist with schema applied (run canoe-base first).
    if not os.path.exists(cfg.db_dir):
        raise FileNotFoundError(
            f"Database not found: {cfg.db_dir!r}. "
            "Create the database with canoe-base before running canoe-residential."
        )

    # Step 0: validate config against global tables already in the DB.
    with sqlite3.connect(cfg.db_dir) as conn:
        validate_db_against_config(runtime, conn)

    # Aggregate subsectors
    with sqlite3.connect(cfg.db_dir) as conn:
        all_subsectors.aggregate(runtime, conn)

    # Convert data costs to final currency
    # currency_conversion.convert_currencies(runtime, conn)

    if cfg.simplify_model:
        model_reduction.simplify_model()
    if cfg.clone_to_xlsx:
        utils.database_converter().clone_sqlite_to_excel(
            from_sqlite_file=cfg.db_dir,
            to_excel_file=cfg.excel_output,
            excel_template_file=cfg.excel_template,
        )

    print(f"Residential sector aggregated into {os.path.basename(cfg.db_dir)}\n")

    # Show any plots that have been made
    if cfg.show_plots:
        save_plots()


def save_plots(output_dir='output_plots'):
    os.makedirs(output_dir, exist_ok=True)
    print("Finished and saving plots.")
    for fig_num in pp.get_fignums():
        fig = pp.figure(fig_num)
        # Try suptitle first, then first axes title, then fall back to figure number
        title = fig.get_suptitle()
        if not title and fig.axes:
            title = fig.axes[0].get_title()
        filename = title if title else f"figure_{fig_num}"
        # Sanitize filename: replace characters that are invalid in Windows filenames
        filename = re.sub(r'[\\/:*?"<>|\x00-\x1f .,]', '_', filename)
        filepath = os.path.join(output_dir, f"{filename}.pdf")
        fig.savefig(filepath, bbox_inches='tight')
        print(f"Saved {filepath}")


if __name__ == "__main__":
    build_database()
