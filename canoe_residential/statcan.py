"""
Statistics Canada data acquisition for canoe-residential.

Public functions return plain pandas DataFrames and take explicit cache/download
parameters so this module can be imported without triggering setup.py's config
singleton.

Source: StatCan Web Data Service REST API
  https://www150.statcan.gc.ca/t1/wds/rest/
"""

import os
import urllib.request
import zipfile

import pandas as pd
import requests


def get_statcan_table(
    table: int,
    cache_dir: str,
    force_download: bool,
    save_as: str | None = None,
    **kwargs,
) -> pd.DataFrame | None:
    """
    Fetch a Statistics Canada table by numeric ID, with local CSV caching.

    Source: StatCan WDS REST API (getFullTableDownloadCSV endpoint)
    Returns: DataFrame with one row per record; index is the CSV row index.
    Cache: {cache_dir}/statcan_{table}.csv  (or {cache_dir}/{save_as})
    """
    if save_as is None:
        save_as = f"statcan_{table}.csv"
    if os.path.splitext(save_as)[1] != ".csv":
        save_as += ".csv"

    cache_file = os.path.join(cache_dir, save_as)

    if not force_download and os.path.isfile(cache_file):
        try:
            df = pd.read_csv(cache_file, index_col=0)
            print(f"Got StatCan table {table} from local cache.")
            return df
        except Exception:
            print(
                f"Could not load StatCan table {table} from cache. Downloading instead."
            )

    url = (
        f"https://www150.statcan.gc.ca/t1/wds/rest/"
        f"getFullTableDownloadCSV/{table}/en"
    )
    response = requests.get(url).json()

    if response["status"] != "SUCCESS":
        print(
            f"StatCan request for table {table} failed. Status: {response['status']}"
        )
        return None

    print(f"Downloading StatCan table {table}...")
    filehandle, _ = urllib.request.urlretrieve(response["object"])
    with zipfile.ZipFile(filehandle, "r") as zf:
        with zf.open(f"{table}.csv", "r") as f:
            df = pd.read_csv(f, **kwargs)

    df.to_csv(cache_file)
    print(f"Cached StatCan table {table} to {save_as}.")
    return df


def load_population_projections(
    regions: pd.DataFrame,
    cache_dir: str,
    force_download: bool,
) -> dict[str, pd.DataFrame]:
    """
    Build provincial population series from StatCan tables 17100009 and 17100057.

    Sources:
      - StatCan 17100009: Historical quarterly provincial population
      - StatCan 17100057: Population projections by scenario / gender / age
        (M1 medium-growth scenario used here)

    Args:
        regions: DataFrame indexed by region code; must have columns 'description'
                 (province name matching StatCan GEO field) and 'include' (bool).
        cache_dir: directory for local CSV caches.
        force_download: if True, skip the local cache and re-download.

    Returns:
        Dict mapping region code -> DataFrame(index=year int, columns=['population']).
    Cache: statcan_17100009.csv, statcan_17100057.csv in cache_dir.
    """
    df_exs = get_statcan_table(17100009, cache_dir, force_download, usecols=[0, 1, 9])
    df_exs = df_exs.loc[df_exs["REF_DATE"].str.contains("-01")]
    df_exs["REF_DATE"] = df_exs["REF_DATE"].str.removesuffix("-01")

    df_proj = get_statcan_table(
        17100057, cache_dir, force_download, usecols=[0, 1, 3, 4, 5, 12]
    )
    df_proj["VALUE"] *= 1000
    df_proj = df_proj.loc[
        (df_proj["Projection scenario"] == "Projection scenario M1: medium-growth")
        & (df_proj["Gender"] == "Total - gender")
        & (df_proj["Age group"] == "All ages")
    ]

    populations: dict[str, pd.DataFrame] = {}
    for region, row in regions.iterrows():
        if not row["include"]:
            continue

        exs = df_exs.loc[
            df_exs["GEO"].str.upper() == row["description"].upper()
        ].dropna()

        prov = df_proj.loc[
            (df_proj["GEO"].str.upper() == row["description"].upper())
            & (df_proj["REF_DATE"] > int(exs["REF_DATE"].values[-1]))
        ].dropna()

        ca = df_proj.loc[
            (df_proj["GEO"].str.upper() == "CANADA")
            & (df_proj["REF_DATE"] >= int(prov["REF_DATE"].values[-1]))
        ].dropna()
        ca["VALUE"] = (
            ca["VALUE"].iloc[1:] * prov["VALUE"].values[-1] / ca["VALUE"].values[0]
        )
        ca.dropna(inplace=True)

        data = [
            *exs["VALUE"].to_list(),
            *prov["VALUE"].to_list(),
            *ca["VALUE"].to_list(),
        ]
        pop = pd.DataFrame(
            index=range(int(exs["REF_DATE"].values[0]), int(ca["REF_DATE"].values[-1]) + 1),
            data=[int(d) for d in data],
            columns=["population"],
        )
        pop.index.rename("year", inplace=True)
        populations[str(region)] = pop

    return populations
