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


GBD_DIR = "ihme_gbd"  # data/raw/ihme_gbd/  – ručně stažené CSV/ZIP z GBD Results Tool

GBD_LOCATIONS = {  # název lokality v GBD -> kód Eurostatu
    "austria": "AT", "belgium": "BE", "bulgaria": "BG", "cyprus": "CY", "czechia": "CZ",
    "czech republic": "CZ", "germany": "DE", "denmark": "DK", "estonia": "EE",
    "greece": "EL", "spain": "ES", "finland": "FI", "france": "FR", "croatia": "HR",
    "hungary": "HU", "ireland": "IE", "italy": "IT", "lithuania": "LT", "luxembourg": "LU",
    "latvia": "LV", "malta": "MT", "netherlands": "NL", "poland": "PL", "portugal": "PT",
    "romania": "RO", "sweden": "SE", "slovenia": "SI", "slovakia": "SK", "norway": "NO",
    "iceland": "IS", "switzerland": "CH",
}
GBD_CAUSES = {"depressive disorders": "dep", "anxiety disorders": "anx"}

GBD_HOWTO = """\
Data IHME GBD 2023 nejdou stáhnout automaticky: OWID je označuje jako
"non-redistributable" (CSV vrací 403) a IHME vyžaduje bezplatný účet.
Stáhni je ručně (cca 5 min):
  1. https://vizhub.healthdata.org/gbd-results/  (přihlásit se / zaregistrovat zdarma)
  2. GBD Estimate: Cause of death or injury | Measure: Prevalence | Metric: Rate
     Cause: Depressive disorders + Anxiety disorders
     Location: všech 27 zemí EU | Age: Age-standardized, All ages, 15-19 years,
     20-24 years, 25-29 years | Sex: Both | Year: 2011–2023
  3. Download -> CSV. Stažený ZIP (nebo rozbalené CSV) dej do složky
     {path}
  4. Spusť znovu: python build_dataset.py --only gbd  (a pak celé bez --only)"""


def _read_gbd_files(folder) -> pd.DataFrame:
    import zipfile
    frames = []
    for f in sorted(folder.glob("*")):
        if f.suffix.lower() == ".csv":
            frames.append(pd.read_csv(f))
        elif f.suffix.lower() == ".zip":
            with zipfile.ZipFile(f) as z:
                for n in z.namelist():
                    if n.lower().endswith(".csv"):
                        frames.append(pd.read_csv(z.open(n)))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    # GBD Results Tool dává sloupce buď "location_name", nebo jen "location"
    df.columns = [c.lower().removesuffix("_name") for c in df.columns]
    return df.drop_duplicates()


def _gbd_age_label(age: str) -> str | None:
    a = str(age).lower()
    if "standardized" in a:
        return "agestd"
    if a.startswith("all ages"):
        return "allages"
    m = re.match(r"(\d+)\s*(?:-|–|to)\s*(\d+)", a)
    if m:
        return f"{m.group(1)}_{m.group(2)}"
    m = re.match(r"(\d+)\s*\+|(\d+)\s*plus", a)
    if m:
        return f"{m.group(1) or m.group(2)}plus"
    return None


def load_gbd() -> pd.DataFrame:
    """Prevalence depresivních a úzkostných poruch (% populace), IHME GBD.

    Zdroj: ručně stažený export z GBD Results Tool v data/raw/ihme_gbd/ (viz GBD_HOWTO).
    Metric "Rate" (na 100 000) se převádí na % (/1000), stejně jako to dělá OWID.
    - dep_agestd, anx_agestd : věkově standardizovaná prevalence, obě pohlaví
    - dep_allages            : hrubá prevalence (všechny věky)
    - dep_15_19, dep_20_24, dep_25_29 (a stejně anx_*) : podle věku
    """
    from .config import RAW_DIR
    folder = RAW_DIR / GBD_DIR
    folder.mkdir(parents=True, exist_ok=True)
    df = _read_gbd_files(folder)
    if df.empty:
        raise DataSourceError(GBD_HOWTO.format(path=folder))
    need = {"measure", "location", "sex", "age", "cause", "metric", "year", "val"}
    if not need <= set(df.columns):
        raise DataSourceError(f"Neočekávané sloupce v exportu GBD: {list(df.columns)}")

    df = df[df["measure"].str.lower().str.startswith("prevalence")
            & df["sex"].str.lower().eq("both")]
    metrics = set(df["metric"].str.lower())
    if "rate" in metrics:
        df = df[df["metric"].str.lower() == "rate"].assign(val=lambda d: d["val"] / 1000)
    else:
        raise DataSourceError(f"V exportu GBD chybí metric 'Rate' (je tam: {metrics}). "
                              "'Percent' v GBD znamená podíl na všech příčinách, ne prevalenci.")
    df = df.assign(geo=df["location"].str.lower().str.replace(r"\s*\(.*\)", "", regex=True)
                   .map(GBD_LOCATIONS),
                   short=df["cause"].str.lower().map(GBD_CAUSES),
                   age_label=df["age"].map(_gbd_age_label))
    unknown = sorted(set(df.loc[df["geo"].isna(), "location"]))
    if unknown:
        print(f"    ! neznámé lokality GBD (ignoruji): {unknown[:10]}")
    df = df.dropna(subset=["geo", "short", "age_label"])
    df = df[df["geo"].isin(COUNTRIES)]
    df["variable"] = df["short"] + "_" + df["age_label"]
    if df.duplicated(["geo", "year", "variable"]).any():
        raise DataSourceError("Export GBD obsahuje duplicitní řádky (víc verzí/odhadů?)")
    out = df.pivot(index=["geo", "year"], columns="variable", values="val").reset_index()
    out.columns.name = None
    print(f"    proměnné GBD: {[c for c in out.columns if c not in ('geo', 'year')]}")
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
    # OWID: sloupec "Month" ve tvaru "YYYY-MM" (dříve "Day" ve tvaru "YYYY-MM-DD")
    date_col = next((c for c in t.columns if c.lower() in ("month", "day", "date")), None)
    vcols = [c for c in _owid_values(t) if c.lower() != "year"]
    monthly = next((c for c in vcols if "month" in c.lower()), vcols[0])
    if date_col:
        d = pd.to_datetime(t[date_col].astype(str).str[:7], format="%Y-%m")
        t = t.assign(year=d.dt.year, month=d.dt.month)
        g = t.groupby(["geo", "year"])[monthly].agg(["mean", "count"])
        # meteorologická zima roku Y = prosinec Y-1 + leden Y + únor Y
        w = t[t["month"].isin([1, 2, 12])].copy()
        w["wyear"] = w["year"] + (w["month"] == 12).astype(int)
        wg = w.groupby(["geo", "wyear"])[monthly].agg(["mean", "count"])
        wg.index.names = ["geo", "year"]
        temp = pd.DataFrame({"temp_mean_c": g["mean"].where(g["count"] == 12)})
        temp = temp.join(wg["mean"].where(wg["count"] == 3).rename("temp_winter_c"),
                         how="outer").reset_index()
    else:  # kdyby OWID vrátil rovnou roční hodnoty
        temp = t.rename(columns={"Year": "year", monthly: "temp_mean_c"})[["geo", "year", "temp_mean_c"]]

    # srážky: roční úhrn v mm
    p = _owid_csv("average-precipitation-per-year", "", "owid_precipitation_annual.csv")
    p = _iso3_to_geo(p, "Code")
    pv = _owid_values(p)[0]
    prec = p.rename(columns={"Year": "year", pv: "precip_mm"})[["geo", "year", "precip_mm"]]

    out = restrict_years(temp.merge(prec, on=["geo", "year"], how="outer"))
    out["weather_src"] = "era5_country"
    # Malta (a jiné malé státy) v OWID chybí – ERA5 maska zemí je moc hrubá.
    # Doplníme bodovými daty ERA5 z Open-Meteo pro hlavní město.
    wvars = ["temp_mean_c", "temp_winter_c", "precip_mm"]
    has = out.groupby("geo")[wvars].count().min(axis=1)
    missing = sorted(g for g in COUNTRIES if has.get(g, 0) == 0)
    if missing:
        print(f"    ! OWID nemá (všechny) údaje pro {missing} -> doplňuji z Open-Meteo (hlavní město)")
        fill = [f for f in (_openmeteo_weather_point(g) for g in missing) if f is not None]
        if fill:
            out = out[~out["geo"].isin(missing)]  # celá země z jednoho zdroje
            out = pd.concat([out] + fill, ignore_index=True)
    return out


def _openmeteo_weather_point(geo: str) -> pd.DataFrame | None:
    from .config import RAW_DIR
    name, lat, lon = CAPITALS[geo]
    params = {"latitude": lat, "longitude": lon,
              "start_date": f"{YEAR_MIN - 1}-12-01", "end_date": f"{YEAR_MAX}-12-31",
              "daily": "temperature_2m_mean,precipitation_sum", "timezone": "GMT"}
    try:
        path = fetch("https://archive-api.open-meteo.com/v1/archive",
                     f"openmeteo_weather_{geo}_{YEAR_MIN}_{YEAR_MAX}.json", params=params, retries=6)
    except DataSourceError as e:
        print(f"    ! {geo}: {e}")
        return None
    d = pd.DataFrame(json.loads(path.read_text())["daily"])
    d["time"] = pd.to_datetime(d["time"])
    d["year"], d["month"] = d["time"].dt.year, d["time"].dt.month
    w = d[d["month"].isin([12, 1, 2])].copy()
    w["year"] = w["year"] + (w["month"] == 12).astype(int)
    out = pd.DataFrame({
        "temp_mean_c": d.groupby("year")["temperature_2m_mean"].mean(),
        "temp_winter_c": w.groupby("year")["temperature_2m_mean"].mean(),
        "precip_mm": d.groupby("year")["precipitation_sum"].sum(),
    }).reset_index()
    out["geo"], out["weather_src"] = geo, "era5_capital_point"
    return restrict_years(out)


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
