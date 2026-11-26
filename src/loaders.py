"""Načtení jednotlivých datasetů.

Každá funkce load_* stáhne (nebo vezme z cache) jeden zdroj a vrátí tabulku
se sloupci geo (kód země Eurostatu), year a proměnnými daného zdroje.
"""
from __future__ import annotations

import io
import json
import re
import time

import pandas as pd

from .config import COUNTRIES, CAPITALS, ISO2_TO_ISO3, ISO3_TO_ISO2, YEAR_MIN, YEAR_MAX
from .utils import (DataSourceError, check_single_valued, eurostat, fetch,
                    interpolate_within_country, restrict_years, select)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


def _iso3_to_geo(df: pd.DataFrame, col: str) -> pd.DataFrame:
    df = df[df[col].isin(ISO3_TO_ISO2)].copy()
    df["geo"] = df[col].map(ISO3_TO_ISO2)
    return df[df["geo"].isin(COUNTRIES)]


# =========================================================================
# 1) Sociální sítě – Eurostat isoc_ci_ac_i, ukazatel I_IUSNET
# =========================================================================
def load_social_media() -> pd.DataFrame:
    """% lidí, kteří za poslední 3 měsíce používali sociální sítě.

    Pozn.: rok 2012 se nesbíral -> doplněn lineární interpolací (sloupce *_interp).
    """
    groups = {"IND_TOTAL": "sm_total", "Y16_24": "sm_16_24", "Y16_29": "sm_16_29"}
    df = eurostat("isoc_ci_ac_i",
                  {"indic_is": "I_IUSNET", "unit": "PC_IND", "ind_type": list(groups)})
    df = select(df, indic_is="I_IUSNET", unit="PC_IND", ind_type=list(groups))
    check_single_valued(df, keep=["ind_type"])
    wide = (df.pivot_table(index=["geo", "year"], columns="ind_type", values="value")
              .rename(columns=groups).reset_index())
    wide.columns.name = None
    wide = restrict_years(wide)
    # doplnit chybějící roky (2012) do mřížky, aby šly interpolovat
    grid = pd.MultiIndex.from_product([wide["geo"].unique(), range(YEAR_MIN, YEAR_MAX + 1)],
                                      names=["geo", "year"]).to_frame(index=False)
    wide = grid.merge(wide, on=["geo", "year"], how="left")
    return interpolate_within_country(wide, list(groups.values()))


# =========================================================================
# 2) Duševní zdraví – IHME GBD 2023 přes Our World in Data
# =========================================================================
OWID = "https://ourworldindata.org/grapher/"


def _owid_csv(slug: str, query: str, cache_name: str) -> pd.DataFrame:
    url = f"{OWID}{slug}.csv?v=1&csvType=full&useColumnShortNames=false{query}"
    path = fetch(url, cache_name)
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    return df


def _owid_values(df: pd.DataFrame) -> list[str]:
    ids = {"entity", "code", "year", "day"}
    return [c for c in df.columns if c.lower() not in ids
            and pd.api.types.is_numeric_dtype(df[c])]


def load_gbd() -> pd.DataFrame:
    """Prevalence depresivních a úzkostných poruch (% populace).

    - *_agestd : věkově standardizovaná prevalence, všechny věky, obě pohlaví
    - *_<věk>  : prevalence podle věkových skupin (které skupiny OWID nabízí,
                 vypíše se při načtení – zkontrolujte, zda jsou tam 15-19, 20-24)
    """
    out = None
    for disorder, short in [("depressive_disorders", "dep"), ("anxiety_disorders", "anx")]:
        # a) věkově standardizovaná
        a = _owid_csv("prevalence-of-mental-disorders",
                      f"&age=all_ages&disorder={disorder}&metric=share_age_standardized&sex=both",
                      f"owid_gbd_{short}_agestd.csv")
        vcols = _owid_values(a)
        a = _iso3_to_geo(a, "Code")[["geo", "Year", vcols[0]]]
        a.columns = ["geo", "year", f"{short}_agestd"]

        # b) podle věku – každý sloupec = jedna věková skupina
        b = _owid_csv("mental-disorders-prevalence-by-age", f"&disorder={disorder}",
                      f"owid_gbd_{short}_by_age.csv")
        vcols = _owid_values(b)
        print(f"    věkové sloupce ({short}): {vcols}")
        b = _iso3_to_geo(b, "Code")
        rename = {}
        for c in vcols:
            m = re.search(r"(\d+)\s*[-–]\s*(\d+)|(\d+)\+|all ages|age-standardized", c, re.I)
            label = _slug(m.group(0).replace("+", "plus")) if m else _slug(c)[-20:]
            rename[c] = f"{short}_{label}"
        b = b[["geo", "Year"] + vcols].rename(columns=rename | {"Year": "year"})

        part = a.merge(b, on=["geo", "year"], how="outer")
        out = part if out is None else out.merge(part, on=["geo", "year"], how="outer")
    return restrict_years(out)


# =========================================================================
# 3) Sebevraždy – Eurostat hlth_cd_asdr2 (standardizované) + hlth_cd_acdr2 (podle věku)
# =========================================================================
SUICIDE = "X60-X84_Y870"


def load_suicide() -> pd.DataFrame:
    df = eurostat("hlth_cd_asdr2", {"icd10": SUICIDE, "unit": "RT", "age": "TOTAL",
                                    "sex": ["T", "M", "F"]})
    df = select(df, icd10=SUICIDE, unit="RT", age="TOTAL", sex=["T", "M", "F"])
    check_single_valued(df, keep=["sex"])
    std = df.pivot_table(index=["geo", "year"], columns="sex", values="value")
    std.columns = [f"suicide_sdr_{s}" for s in std.columns]

    # hrubá úmrtnost mladých (podle věkových skupin)
    ages = {"Y15-19": "15_19", "Y20-24": "20_24", "Y25-29": "25_29"}
    try:
        y = eurostat("hlth_cd_acdr2", {"icd10": SUICIDE, "unit": "RT", "sex": "T",
                                       "age": list(ages)})
        y = select(y, icd10=SUICIDE, unit="RT", sex="T", age=list(ages))
        if "resid" in y.columns:  # rezidenti vs. všichni – bereme rezidenty, pokud existují
            y = y[y["resid"] == ("TOT_RESID" if "TOT_RESID" in set(y["resid"]) else y["resid"].iloc[0])]
        check_single_valued(y, keep=["age"])
        young = y.pivot_table(index=["geo", "year"], columns="age", values="value")
        young.columns = [f"suicide_cdr_{ages[a]}" for a in young.columns]
        std = std.join(young, how="outer")
    except DataSourceError as e:
        print(f"    ! hlth_cd_acdr2 se nepodařilo načíst, pokračuji bez věkových skupin: {e}")
    return restrict_years(std.reset_index())


# =========================================================================
# 4) Počasí – teplota a srážky (ERA5, průměr za celou zemi, přes OWID)
# =========================================================================
def load_weather_owid() -> pd.DataFrame:
    # teplota: měsíční hodnoty -> roční průměr (jen roky se všemi 12 měsíci)
    t = _owid_csv("average-monthly-surface-temperature", "", "owid_temperature_monthly.csv")
    t = _iso3_to_geo(t, "Code")
    date_col = next((c for c in t.columns if c.lower() == "day"), None)
    vcols = _owid_values(t)
    monthly = next((c for c in vcols if "month" in c.lower()), vcols[0])
    if date_col:
        d = pd.to_datetime(t[date_col])
        t = t.assign(year=d.dt.year, month=d.dt.month)
        winter = t[t["month"].isin([1, 2, 12])].groupby(["geo", "year"])[monthly].mean()
        g = t.groupby(["geo", "year"])[monthly].agg(["mean", "count"])
        temp = pd.DataFrame({"temp_mean_c": g["mean"].where(g["count"] == 12),
                             "temp_winter_c": winter}).reset_index()
    else:  # kdyby OWID vrátil rovnou roční hodnoty
        temp = t.rename(columns={"Year": "year", monthly: "temp_mean_c"})[["geo", "year", "temp_mean_c"]]

    # srážky: roční úhrn v mm
    p = _owid_csv("average-precipitation-per-year", "", "owid_precipitation_annual.csv")
    p = _iso3_to_geo(p, "Code")
    pv = _owid_values(p)[0]
    prec = p.rename(columns={"Year": "year", pv: "precip_mm"})[["geo", "year", "precip_mm"]]

    return restrict_years(temp.merge(prec, on=["geo", "year"], how="outer"))


# =========================================================================
# 5) Sluneční svit – Open-Meteo (ERA5, bod = hlavní město)
# =========================================================================
def load_sunshine() -> pd.DataFrame:
    """Hodiny slunečního svitu za rok a v tmavé polovině roku (říjen–březen).

    Open-Meteo vrací denní hodnoty pro jeden bod, proto bereme hlavní město.
    """
    rows = []
    for geo in COUNTRIES:
        name, lat, lon = CAPITALS[geo]
        params = {"latitude": lat, "longitude": lon,
                  "start_date": f"{YEAR_MIN}-01-01", "end_date": f"{YEAR_MAX}-12-31",
                  "daily": "sunshine_duration,daylight_duration", "timezone": "GMT"}
        cache = f"openmeteo_{geo}_{YEAR_MIN}_{YEAR_MAX}.json"
        try:
            path = fetch("https://archive-api.open-meteo.com/v1/archive", cache, params=params)
        except DataSourceError as e:
            print(f"    ! {geo}: {e}")
            continue
        js = json.loads(path.read_text())
        if "daily" not in js:
            print(f"    ! {geo}: neočekávaná odpověď {str(js)[:200]}")
            path.unlink()  # nesmí zůstat v cache
            continue
        d = pd.DataFrame(js["daily"])
        d["time"] = pd.to_datetime(d["time"])
        d["year"] = d["time"].dt.year
        d["dark"] = d["time"].dt.month.isin([1, 2, 3, 10, 11, 12])
        g = d.groupby("year")
        rows.append(pd.DataFrame({
            "geo": geo,
            "year": g.size().index,
            "sun_hours_year": g["sunshine_duration"].sum().values / 3600,
            "sun_hours_dark": d[d["dark"]].groupby("year")["sunshine_duration"].sum()
                                .reindex(g.size().index).values / 3600,
            "sun_share_of_daylight": (g["sunshine_duration"].sum()
                                      / g["daylight_duration"].sum()).values,
        }))
        time.sleep(0.5)  # šetrnost k API
    if not rows:
        raise DataSourceError("Open-Meteo: nepodařilo se načíst žádnou zemi")
    return restrict_years(pd.concat(rows, ignore_index=True))


# =========================================================================
# 6) Psychiatři na 100 000 obyvatel – Eurostat hlth_rs_physcat
# =========================================================================
def load_psychiatrists() -> pd.DataFrame:
    df = eurostat("hlth_rs_physcat", {"unit": "P_HTHAB"})
    df = select(df, unit="P_HTHAB")
    spec_col = next((c for c in df.columns if df[c].astype(str).eq("PSY").any()), None)
    if spec_col is None:
        raise DataSourceError("V hlth_rs_physcat jsem nenašel kategorii PSY")
    df = df[df[spec_col] == "PSY"]
    check_single_valued(df, keep=[spec_col])
    out = df.groupby(["geo", "year"])["value"].mean().rename("psy_per100k").reset_index()
    out = restrict_years(out)
    # Řada má díry -> interpolace + průměr za období (stabilnější proměnná)
    grid = pd.MultiIndex.from_product([out["geo"].unique(), range(YEAR_MIN, YEAR_MAX + 1)],
                                      names=["geo", "year"]).to_frame(index=False)
    out = interpolate_within_country(grid.merge(out, how="left"), ["psy_per100k"])
    out["psy_per100k_mean"] = out.groupby("geo")["psy_per100k"].transform("mean")
    return out


# =========================================================================
# 7) Ekonomika – HDP na obyvatele + nezaměstnanost mladých (Eurostat)
# =========================================================================
def load_economy() -> pd.DataFrame:
    gdp = eurostat("nama_10_pc", {"unit": "CP_EUR_HAB", "na_item": "B1GQ"})
    gdp = select(gdp, unit="CP_EUR_HAB", na_item="B1GQ")
    gdp = gdp[["geo", "year", "value"]].rename(columns={"value": "gdp_eur_per_cap"})

    un = eurostat("une_rt_a", {"age": "Y15-24", "unit": "PC_ACT", "sex": "T"})
    un = select(un, age="Y15-24", unit="PC_ACT", sex="T")
    check_single_valued(un, keep=[])
    un = un[["geo", "year", "value"]].rename(columns={"value": "unemp_15_24_pct"})
    return restrict_years(gdp.merge(un, on=["geo", "year"], how="outer"))


# =========================================================================
# 8) COVID-19 restrikce – Oxford Stringency Index (OxCGRT)
# =========================================================================
OXCGRT_URL = ("https://raw.githubusercontent.com/OxCGRT/covid-policy-dataset/main/"
              "data/OxCGRT_compact_national_v1.csv")


def load_covid() -> pd.DataFrame:
    path = fetch(OXCGRT_URL, "oxcgrt_compact_national_v1.csv", timeout=600)
    cols = ["CountryCode", "Date", "StringencyIndex_Average"]
    df = pd.read_csv(path, usecols=lambda c: c in cols, low_memory=False)
    df = _iso3_to_geo(df, "CountryCode")
    df["year"] = df["Date"].astype(str).str[:4].astype(int)
    out = (df.groupby(["geo", "year"])["StringencyIndex_Average"].mean()
             .rename("stringency_mean").reset_index())
    # mimo roky 2020–2022 žádná opatření -> 0
    grid = pd.MultiIndex.from_product([COUNTRIES, range(YEAR_MIN, YEAR_MAX + 1)],
                                      names=["geo", "year"]).to_frame(index=False)
    out = grid.merge(out, on=["geo", "year"], how="left")
    out["stringency_mean"] = out["stringency_mean"].fillna(0.0)
    out["covid_period"] = out["year"].between(2020, 2022).astype(int)
    return out


# =========================================================================
# 9) Antidepresiva – OECD Health Statistics (DDD / 1000 obyv. / den, N06A)
# =========================================================================
OECD_URLS = [
    "https://sdmx.oecd.org/public/rest/data/OECD.ELS.HD,DSD_HEALTH_PHMC@DF_KEY_INDIC,/"
    "..DDD_10P3HB..N06A?startPeriod={y0}&dimensionAtObservation=AllDimensions&format=csvfile",
    "https://sdmx.oecd.org/public/rest/data/OECD.ELS.HD,HEALTH_PHMC@DF_KEY_INDIC,/"
    "..DDD_10P3HB..N06A?startPeriod={y0}&dimensionAtObservation=AllDimensions&format=csvfile",
]


def load_antidepressants() -> pd.DataFrame:
    """Pokud API selže: v OECD Data Explorer (odkaz [2] v plánu) dej
    Download -> 'Table in unfiltered CSV format' a ulož jako data/raw/oecd_antidepressants.csv
    """
    path, err = None, None
    for i, url in enumerate(OECD_URLS):
        try:
            path = fetch(url.format(y0=YEAR_MIN), "oecd_antidepressants.csv")
            break
        except DataSourceError as e:
            err = e
    if path is None:
        raise err
    df = pd.read_csv(path)
    if "REF_AREA" not in df.columns or "OBS_VALUE" not in df.columns:
        raise DataSourceError(f"Neočekávané sloupce OECD: {list(df.columns)[:15]}")
    for col, val in [("MEASURE", "DDD_10P3HB"), ("PHARMACEUTICAL", "N06A")]:
        if col in df.columns and (df[col] == val).any():
            df = df[df[col] == val]
    df = _iso3_to_geo(df, "REF_AREA")
    df["year"] = pd.to_numeric(df["TIME_PERIOD"], errors="coerce")
    out = (df.groupby(["geo", "year"])["OBS_VALUE"].mean()
             .rename("antidep_ddd").reset_index())
    return restrict_years(out)


# =========================================================================
# 10) Setkávání s přáteli – EU-SILC ilc_scp09 (jen 2015 a 2022, mimo hlavní panel)
# =========================================================================
def load_friends() -> pd.DataFrame:
    df = eurostat("ilc_scp09", {"sex": "T"}, countries=COUNTRIES)
    df = select(df, sex="T")
    if "isced11" in df.columns:
        df = select(df, isced11="TOTAL")
    ages = [a for a in ["Y16-24", "Y16-29", "Y_GE16"] if a in set(df["age"])]
    df = df[df["age"].isin(ages)]
    keys = [c for c in df.columns if c not in ("geo", "year", "value", "flag", "freq", "unit",
                                               "sex", "isced11")]
    df["variable"] = df[keys].astype(str).agg("_".join, axis=1).map(_slug)
    return df.pivot_table(index=["geo", "year"], columns="variable", values="value").reset_index()
