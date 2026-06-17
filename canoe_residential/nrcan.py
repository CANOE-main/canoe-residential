"""
NRCan data acquisition for canoe-residential.

Public functions take simple inputs (region, table number, URL) and return
plain pandas DataFrames. All network calls, file parsing, and caching live
here; this module imports neither canoe_schema nor anything SQL-related.

Sources:
  - NRCan Comprehensive Energy Use Database (CEUD), "COMPR-DB" residential
    tables, fetched via the URL template in params['nrcan_url'].
  - NRCan Handbook on Energy Consumption (e.g. res_00_16_e.xls), fetched
    directly by URL from subsector modules.
"""

import os
import pickle

import pandas as pd
import requests

import canoe_residential.utils as utils
from canoe_residential.setup import config


def _compr_db_url(region: str, table_number: int) -> str:
    """Build a NRCan COMPR-DB table URL for the given region/table."""
    nrcan_region = config.regions.loc[region, "nrcan_id"]
    return (
        str(config.params["nrcan_url"])
        .replace("<y>", str(config.params["base_year"]))
        .replace("<r>", nrcan_region.lower())
        .replace("<t>", str(table_number))
    )


def get_data(
    url: str,
    file_type: str | None = None,
    cache_file_type: str | None = None,
    name: str | None = None,
    **kwargs,
) -> pd.DataFrame | None:
    """
    Generic downloader with local caching, used for CSV/Excel/XML sources.

    Returns: DataFrame (or parsed dict for XML), or None on failure.
    Cache: {config.cache_dir}/{name}, derived from the URL filename unless given.
    """
    if name is None:
        name = url.split("/")[-1].split("\\")[-1]
    if file_type is None:
        file_type = url.split(".")[-1]
    file_type = file_type.lower()

    if cache_file_type is None:
        if file_type == "xml":
            cache_file_type = "pkl"
        elif "xl" in file_type:
            cache_file_type = "csv"
        else:
            cache_file_type = file_type

    if name.split(".")[-1] != cache_file_type:
        name = os.path.splitext(name)[0] + "." + cache_file_type
    cache_file = config.cache_dir + name

    data = None
    if not config.params["force_download"] and os.path.isfile(cache_file):
        if cache_file_type == "csv":
            data = pd.read_csv(cache_file, index_col=0, dtype="unicode")
        elif cache_file_type == "pkl":
            with open(cache_file, "rb") as file:
                data = pickle.load(file)

        print(f"Got {name} from local cache.")

    else:
        print(f"Downloading {name} ...")

        try:
            if file_type == "csv":
                data = pd.read_csv(url, **kwargs)
            elif "xl" in file_type:
                data = pd.read_excel(url, **kwargs)
            elif file_type == "xml":
                import xmltodict

                data = xmltodict.parse(requests.get(url).content)
        except Exception as e:
            print(f"Failed to download {url}")
            print(e)

        try:
            if not os.path.exists(config.cache_dir):
                os.mkdir(config.cache_dir)

            if cache_file_type == "csv":
                data.to_csv(cache_file)
            elif cache_file_type == "pkl":
                with open(cache_file, "wb") as file:
                    pickle.dump(data, file)
            print(f"Cached {name}.")
        except Exception as e:
            print(f"Failed to cache {cache_file}.")
            print(e)

    return data


def get_compr_db(
    region: str, table_number: int, first_row: int = 0, last_row: int | None = None
) -> pd.DataFrame:
    """
    Fetch a NRCan COMPR-DB residential sector table for one region.

    Source: NRCan Comprehensive Energy Use Database, table `table_number`.
    Returns: DataFrame indexed by technology/category row label, columns = years.
    Cache: {config.cache_dir}/<url-derived filename>.csv
    """
    df = get_data(_compr_db_url(region, table_number), skiprows=10)
    df = df.loc[first_row:] if last_row is None else df.loc[first_row:last_row]
    df = df.drop("Unnamed: 0", axis=1).set_index("Unnamed: 1").dropna()
    df.index.name = None
    utils.clean_index(df)

    df.columns = [int(col) for col in df.columns]
    df = df.astype(float, errors="ignore")

    return df
