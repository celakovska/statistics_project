"""Postupně načte všechny datasety a spojí je do panelu země × rok.

Použití:
    python build_dataset.py                 # vše (stažené soubory se cachují v data/raw)
    python build_dataset.py --only gbd suicide
    python build_dataset.py --force         # stáhnout znovu

Výstupy (data/processed):
    <dataset>.csv          každý zdroj zvlášť
    panel.csv              spojený panel země × rok (hlavní dataset pro analýzu)
    friends_2015_2022.csv  setkávání s přáteli (jen 2 roky, mimo panel)
    coverage.csv           kolik zemí má hodnotu v každém roce pro každou proměnnou
"""
import argparse
import sys
import traceback

import pandas as pd

from src import loaders, utils
from src.config import PROCESSED_DIR, YEAR_MIN, YEAR_MAX, COUNTRIES

# Pořadí kroků = pořadí importu
STEPS = [
    ("social_media",   loaders.load_social_media,   "Eurostat isoc_ci_ac_i – sociální sítě"),
    ("gbd",            loaders.load_gbd,            "IHME GBD 2023 (OWID) – deprese, úzkosti"),
    ("suicide",        loaders.load_suicide,        "Eurostat hlth_cd_asdr2/acdr2 – sebevraždy"),
    ("weather",        loaders.load_weather_owid,   "ERA5 (OWID) – teplota, srážky"),
    ("sunshine",       loaders.load_sunshine,       "Open-Meteo – sluneční svit"),
    ("psychiatrists",  loaders.load_psychiatrists,  "Eurostat hlth_rs_physcat – psychiatři"),
    ("economy",        loaders.load_economy,        "Eurostat nama_10_pc, une_rt_a – HDP, nezaměstnanost"),
    ("covid",          loaders.load_covid,          "OxCGRT – COVID Stringency Index"),
    ("antidepressants", loaders.load_antidepressants, "OECD – spotřeba antidepresiv"),
]
SIDE_STEPS = [
    ("friends", loaders.load_friends, "Eurostat ilc_scp09 – setkávání s přáteli (2015, 2022)"),
]


def describe(name: str, df: pd.DataFrame) -> None:
    vars_ = [c for c in df.columns if c not in ("geo", "year")]
    years = df["year"].dropna()
    print(f"    řádků: {len(df)}, zemí: {df['geo'].nunique()}, "
          f"roky: {int(years.min()) if len(years) else '-'}–{int(years.max()) if len(years) else '-'}")
    miss = df[vars_].isna().mean().mul(100).round(1)
    for v in vars_:
        print(f"      {v:<28} chybí {miss[v]:5.1f} %")
    missing_countries = sorted(set(COUNTRIES) - set(df["geo"]))
    if missing_countries:
        print(f"    ! země bez dat: {missing_countries}")


def run(steps, results, failures):
    for i, (name, func, label) in enumerate(steps, 1):
        print(f"\n[{i}] {name}: {label}")
        try:
            df = func()
            df = df.copy()
            df["year"] = df["year"].astype(int)
            df.to_csv(PROCESSED_DIR / f"{name}.csv", index=False)
            describe(name, df)
            results[name] = df
        except Exception as e:  # jeden zdroj nesmí shodit ostatní
            failures[name] = e
            print(f"    ✗ SELHALO: {e}")
            if "--debug" in sys.argv:
                traceback.print_exc()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="jen vybrané kroky")
    ap.add_argument("--force", action="store_true", help="stáhnout znovu, ignorovat cache")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    utils.FORCE_DOWNLOAD = args.force
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    steps = [s for s in STEPS + SIDE_STEPS if not args.only or s[0] in args.only]
    results, failures = {}, {}
    run(steps, results, failures)

    # --- spojení do panelu ---------------------------------------------------
    panel = utils.full_grid()
    for name, _, _ in STEPS:
        if name in results:
            panel = panel.merge(results[name], on=["geo", "year"], how="left")
        elif not args.only:
            # vezmi starší zpracovanou verzi, pokud existuje
            old = PROCESSED_DIR / f"{name}.csv"
            if old.exists():
                print(f"\n  (panel: {name} z dřívějšího běhu)")
                panel = panel.merge(pd.read_csv(old), on=["geo", "year"], how="left")
    if "friends" in results:
        results["friends"].to_csv(PROCESSED_DIR / "friends_2015_2022.csv", index=False)

    if not args.only:
        panel.to_csv(PROCESSED_DIR / "panel.csv", index=False)
        coverage = (panel.drop(columns="geo").groupby("year").count().T)
        coverage.to_csv(PROCESSED_DIR / "coverage.csv")
        print("\n" + "=" * 70)
        print(f"PANEL: {panel.shape[0]} řádků ({len(COUNTRIES)} zemí × "
              f"{YEAR_MAX - YEAR_MIN + 1} let), {panel.shape[1] - 2} proměnných")
        print("Počet zemí s hodnotou podle roku (výběr):")
        with pd.option_context("display.width", 200, "display.max_columns", 30):
            print(coverage.to_string())

    print("\n" + "=" * 70)
    print(f"OK: {list(results)}")
    if failures:
        print(f"SELHALO: {list(failures)}  -> viz hlášky výše (soubory lze stáhnout ručně do data/raw)")
        sys.exit(1)


if __name__ == "__main__":
    main()
