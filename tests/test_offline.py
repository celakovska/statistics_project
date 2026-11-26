"""Offline test celé pipeline: nahradí internet simulovanými odpověďmi ve stejném
formátu, jaký vracejí skutečná API (Eurostat JSON-stat, OWID CSV, Open-Meteo JSON,
OxCGRT CSV, OECD SDMX CSV). Ověřuje parsování, filtry a spojení do panelu.

Spuštění:  python -m pytest tests/  nebo  python tests/test_offline.py
"""
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import config, utils  # noqa: E402

rng = np.random.default_rng(0)
YEARS = list(range(2010, 2025))


class FakeResp:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


def jsonstat(dims: dict, skip=lambda codes: False) -> bytes:
    ids, sizes = list(dims), [len(v) for v in dims.values()]
    value, status = {}, {}
    for flat, combo in enumerate(np.ndindex(*sizes)):
        codes = {d: dims[d][i] for d, i in zip(ids, combo)}
        if skip(codes):
            continue
        value[str(flat)] = round(float(rng.uniform(1, 100)), 2)
    js = {"version": "2.0", "class": "dataset", "id": ids, "size": sizes,
          "dimension": {d: {"category": {"index": {c: i for i, c in enumerate(v)}}}
                        for d, v in dims.items()},
          "value": value, "status": status}
    return json.dumps(js).encode()


def fake_eurostat(code: str, params: list) -> bytes:
    p = {}
    for k, v in params:
        p.setdefault(k, []).append(v)
    dims = {"freq": ["A"]}
    for k, v in p.items():
        if k not in ("format", "lang", "sinceTimePeriod", "geo"):
            dims[k] = v
    extra = {"hlth_rs_physcat": {"med_spec": ["PSY", "MED", "SURG"]},
             "hlth_cd_acdr2": {"resid": ["TOT_IN", "TOT_RESID"]},
             "ilc_scp09": {"frequenc": ["DAY", "WEEK"], "ind_type": ["FAM_REL", "FRD"],
                           "age": ["Y16-24", "Y_GE16", "Y25-34"], "isced11": ["TOTAL", "ED0-2"]}}
    dims.update(extra.get(code, {}))
    dims["geo"] = p["geo"]
    years = [2015, 2022] if code == "ilc_scp09" else YEARS
    dims["time"] = [str(y) for y in years]
    # simulace děr: soc. sítě nemají 2012, psychiatři mají díry
    skip = lambda c: ((code == "isoc_ci_ac_i" and c["time"] == "2012")
                      or (code == "hlth_rs_physcat" and c["geo"] in ("CZ", "SK") and c["time"] < "2017")
                      or (code.startswith("hlth_cd") and c["time"] > "2023"))
    return jsonstat(dims, skip)


def owid_csv(path: str, query: dict) -> bytes:
    iso3 = list(config.ISO2_TO_ISO3.values()) + ["OWID_WRL"]
    rows = []
    if "by-age" in path:
        cols = ["Share ages 10-14", "Share ages 15-19", "Share ages 20-24", "Share ages 70+"]
        for c in iso3:
            for y in range(1990, 2024):
                rows.append({"Entity": c, "Code": c, "Year": y, **{k: rng.uniform(1, 10) for k in cols}})
    elif "monthly-surface-temperature" in path:
        for c in iso3:
            for d in pd.date_range("2010-01-15", "2026-06-15", freq="MS"):
                rows.append({"Entity": c, "Code": c, "Year": d.year, "Day": d.strftime("%Y-%m-%d"),
                             "Monthly average": rng.normal(10, 8), "Annual average": 10.0})
    else:
        for c in iso3:
            for y in range(1990, 2026):
                rows.append({"Entity": c, "Code": c, "Year": y, "value col": rng.uniform(1, 1000)})
    return pd.DataFrame(rows).to_csv(index=False).encode()


def openmeteo(params: dict) -> bytes:
    days = pd.date_range(params["start_date"], params["end_date"])
    return json.dumps({"daily": {"time": days.strftime("%Y-%m-%d").tolist(),
                                 "sunshine_duration": rng.uniform(0, 40000, len(days)).tolist(),
                                 "daylight_duration": [50000.0] * len(days)}}).encode()


def oxcgrt() -> bytes:
    rows = [{"CountryCode": c, "Date": int(d.strftime("%Y%m%d")),
             "StringencyIndex_Average": rng.uniform(0, 90), "Other": 1}
            for c in config.ISO2_TO_ISO3.values() for d in pd.date_range("2020-01-01", "2022-12-31", freq="7D")]
    return pd.DataFrame(rows).to_csv(index=False).encode()


def oecd() -> bytes:
    rows = [{"REF_AREA": c, "MEASURE": "DDD_10P3HB", "PHARMACEUTICAL": "N06A",
             "TIME_PERIOD": y, "OBS_VALUE": rng.uniform(20, 160)}
            for c in list(config.ISO2_TO_ISO3.values())[:24] for y in range(2011, 2025)]
    return pd.DataFrame(rows).to_csv(index=False).encode()


def fake_get(url, params=None, headers=None, timeout=None):
    u = urlparse(url)
    q = dict(parse_qsl(u.query))
    if "eurostat" in url:
        return FakeResp(fake_eurostat(u.path.rsplit("/", 1)[-1], params))
    if "ourworldindata" in u.netloc:
        return FakeResp(owid_csv(u.path, q))
    if "open-meteo" in u.netloc:
        return FakeResp(openmeteo(params))
    if "OxCGRT" in url:
        return FakeResp(oxcgrt())
    if "oecd" in u.netloc:
        return FakeResp(oecd())
    raise AssertionError(f"neočekávaná URL {url}")


def test_pipeline(monkeypatch=None):
    tmp = Path(tempfile.mkdtemp())
    orig = (utils.RAW_DIR, utils.requests.get)
    try:
        import build_dataset
        from src import loaders
        utils.RAW_DIR = tmp / "raw"
        build_dataset.PROCESSED_DIR = tmp / "processed"
        utils.requests.get = fake_get
        loaders.time.sleep = lambda s: None
        sys.argv = ["build_dataset.py"]
        build_dataset.main()

        panel = pd.read_csv(tmp / "processed" / "panel.csv")
        assert len(panel) == len(config.COUNTRIES) * (config.YEAR_MAX - config.YEAR_MIN + 1)
        assert not panel.duplicated(["geo", "year"]).any()
        # 2012 chybí v původních datech, interpolace ho doplní
        cz12 = panel[(panel.geo == "CZ") & (panel.year == 2012)].iloc[0]
        assert pd.isna(cz12["sm_16_24"]) and pd.notna(cz12["sm_16_24_interp"])
        for col in ["dep_agestd", "dep_15_19", "anx_20_24", "suicide_sdr_T", "suicide_cdr_15_19",
                    "temp_mean_c", "precip_mm", "sun_hours_year", "psy_per100k_mean",
                    "gdp_eur_per_cap", "unemp_15_24_pct", "stringency_mean", "antidep_ddd"]:
            assert col in panel.columns, col
            assert panel[col].notna().any(), col
        assert (panel.loc[panel.year == 2019, "stringency_mean"] == 0).all()
        assert (tmp / "processed" / "friends_2015_2022.csv").exists()
        print("\nOFFLINE TEST OK")
    finally:
        utils.RAW_DIR, utils.requests.get = orig
        shutil.rmtree(tmp)


if __name__ == "__main__":
    test_pipeline()
