"""
Builds residential buildings sector database
Written by Ian David Elder for the CANOE model
"""

import os
import canoe_residential.all_subsectors as all_subsectors
import canoe_residential.utils as utils
import re
import canoe_residential.model_reduction as model_reduction
from canoe_residential.setup import config
from matplotlib import pyplot as pp



def build_database():

    print(f"Aggregating residential sector into {os.path.basename(config.database_file)}...\n")

    # DB must already exist with schema applied (run canoe-base first).
    if not os.path.exists(config.database_file):
        raise FileNotFoundError(
            f"Database not found: {config.database_file!r}. "
            "Create the database with canoe-base before running canoe-residential."
        )

    # Aggregate subsectors
    all_subsectors.aggregate()

    # Convert data costs to final currency
    # currency_conversion.convert_currencies()

    if config.params['simplify_model']: model_reduction.simplify_model()
    if config.params['clone_to_xlsx']: utils.database_converter().clone_sqlite_to_excel()

    #prep_high_res_testing()

    print(f"Residential sector aggregated into {os.path.basename(config.database_file)}\n")

    # Show any plots that have been made
    if config.params['show_plots']:
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