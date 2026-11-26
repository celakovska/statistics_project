"""Pomocné funkce: stahování s cache, parser Eurostat JSON-stat, filtrování."""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .config import RAW_DIR, YEAR_MIN, YEAR_MAX, COUNTRIES

EUROSTAT_API = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
HEADERS = {"User-Agent": "Our World In Data data fetch/1.0 (SAN student project)"}

# Když True, soubory se stáhnou znovu i když už jsou v data/raw.
FORCE_DOWNLOAD = False


class DataSourceError(RuntimeError):
    pass


def fetch(url: str, cache_name: str, params=None, timeout: int = 120, retries: int = 4) -> Path:
    """Stáhne URL do data/raw/<cache_name>. Pokud soubor existuje, použije cache.

    Když stahování selže, je možné soubor stáhnout ručně v prohlížeči
    a uložit ho přesně pod tímto jménem do data/raw/.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / cache_name
    if path.exists() and path.stat().st_size > 0 and not FORCE_DOWNLOAD:
        print(f"    [cache] {path.relative_to(RAW_DIR.parent.parent)}")
        return path

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            print(f"    [download] {url}")
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            if r.status_code == 429:  # rate limit (Open-Meteo) -> počkat a zkusit znovu
                wait = int(r.headers.get("Retry-After", 0) or 0) or 20 * attempt
                print(f"    pokus {attempt}/{retries}: 429 Too Many Requests, čekám {wait} s")
                last_err = requests.HTTPError("429 Too Many Requests")
                time.sleep(wait)
                continue
            r.raise_for_status()
            if not r.content:
                raise requests.RequestException("prázdná odpověď")
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(r.content)
            tmp.replace(path)  # do cache jen kompletní soubor
            return path
        except requests.HTTPError as e:
            last_err = e
            code = e.response.status_code if e.response is not None else None
            print(f"    pokus {attempt}/{retries} selhal: {e}")
            if code is not None and 400 <= code < 500:
                break  # 403/404 se opakováním nespraví
            time.sleep(3 * attempt)
        except requests.RequestException as e:
            last_err = e
            print(f"    pokus {attempt}/{retries} selhal: {e}")
            time.sleep(3 * attempt)
    raise DataSourceError(
        f"Nepodařilo se stáhnout {url}\n"
        f"  -> Stáhni soubor ručně a ulož ho jako {path}\n  Chyba: {last_err}"
    )


# ---------------------------------------------------------------- Eurostat --
def parse_jsonstat(js: dict) -> pd.DataFrame:
    """Převede odpověď Eurostat API (JSON-stat 2.0) na dlouhou tabulku."""
    ids = js["id"]
    sizes = js["size"]
    dims = js["dimension"]

    # pro každou dimenzi: seznam kódů seřazený podle pozice
    codes = []
    for d in ids:
        index = dims[d]["category"]["index"]
        if isinstance(index, list):
            codes.append(index)
        else:
            ordered = sorted(index.items(), key=lambda kv: kv[1])
            codes.append([k for k, _ in ordered])

    values = js.get("value", {})
    status = js.get("status", {}) or {}
    if isinstance(values, list):  # hustý formát
        values = {str(i): v for i, v in enumerate(values) if v is not None}
    if isinstance(status, list):
        status = {str(i): s for i, s in enumerate(status) if s}

    if not values:
        return pd.DataFrame(columns=ids + ["value", "flag"])

    flat = np.array([int(k) for k in values.keys()])
    multi = np.unravel_index(flat, sizes)
    df = pd.DataFrame({d: np.asarray(codes[i])[multi[i]] for i, d in enumerate(ids)})
    df["value"] = pd.to_numeric(list(values.values()), errors="coerce")
    df["flag"] = [status.get(str(k), "") for k in flat]
    return df


def eurostat(code: str, filters: dict | None = None, cache_name: str | None = None,
             countries: list[str] = COUNTRIES) -> pd.DataFrame:
    """Stáhne dataset z Eurostat API a vrátí dlouhou tabulku (geo, year, ..., value)."""
    params = [("format", "JSON"), ("lang", "EN"), ("sinceTimePeriod", str(YEAR_MIN - 1))]
    for g in countries:
        params.append(("geo", g))
    for k, v in (filters or {}).items():
        for val in (v if isinstance(v, (list, tuple)) else [v]):
            params.append((k, val))

    path = fetch(EUROSTAT_API + code, cache_name or f"eurostat_{code}.json", params=params)
    js = json.loads(path.read_text(encoding="utf-8"))
    if "error" in js:
        raise DataSourceError(f"Eurostat vrátil chybu pro {code}: {js['error']}")
    df = parse_jsonstat(js)
    df = df.rename(columns={"time": "year"})
    df["year"] = pd.to_numeric(df["year"].astype(str).str[:4], errors="coerce").astype("Int64")
    return df


def select(df: pd.DataFrame, **conditions) -> pd.DataFrame:
    """Filtruje tabulku podle dimenzí; hlídá, že kódy existují (jinak vypíše dostupné)."""
    for dim, vals in conditions.items():
        vals = vals if isinstance(vals, (list, tuple)) else [vals]
        if dim not in df.columns:
            print(f"    ! dimenze '{dim}' v datech není (dostupné: {list(df.columns)})")
            continue
        out = df[df[dim].isin(vals)]
        if out.empty:
            raise DataSourceError(
                f"Žádná data pro {dim}={vals}. Dostupné kódy: {sorted(df[dim].unique())[:50]}")
        df = out
    return df


def check_single_valued(df: pd.DataFrame, keep: list[str]) -> None:
    """Upozorní, pokud nějaká nefiltrovaná dimenze má víc hodnot (hrozí duplicity)."""
    for c in df.columns:
        if c in keep or c in ("value", "flag", "geo", "year", "freq"):
            continue
        n = df[c].nunique()
        if n > 1:
            print(f"    ! dimenze '{c}' má {n} hodnot: {sorted(df[c].unique())[:10]} – zvaž filtr")


def restrict_years(df: pd.DataFrame) -> pd.DataFrame:
    return df[(df["year"] >= YEAR_MIN) & (df["year"] <= YEAR_MAX)]


def interpolate_within_country(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Lineární interpolace chybějících let uvnitř každé země (ne extrapolace)."""
    df = df.sort_values(["geo", "year"]).copy()
    for c in cols:
        df[c + "_interp"] = (df.groupby("geo")[c]
                               .transform(lambda s: s.interpolate(limit_area="inside")))
    return df


def full_grid() -> pd.DataFrame:
    """Kostra panelu: všechny země × všechny roky."""
    return pd.MultiIndex.from_product(
        [COUNTRIES, range(YEAR_MIN, YEAR_MAX + 1)], names=["geo", "year"]
    ).to_frame(index=False)
