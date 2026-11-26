"""Společné nastavení projektu: země, roky, cesty."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"            # stažené soubory (cache), negitují se
PROCESSED_DIR = ROOT / "data" / "processed"  # zpracované tabulky

# Sledované období. Data o sociálních sítích začínají 2011 (2012 chybí),
# GBD a sebevraždy končí 2023.
YEAR_MIN = 2011
YEAR_MAX = 2023

# Země EU27 v kódování Eurostatu (POZOR: Řecko je v Eurostatu "EL", ne "GR").
EU27 = ["AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "ES", "FI", "FR",
        "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO",
        "SE", "SI", "SK"]
# Volitelně lze přidat EFTA země (mají data o soc. sítích jen částečně).
EXTRA = ["NO", "IS", "CH"]
INCLUDE_EXTRA = False
COUNTRIES = EU27 + (EXTRA if INCLUDE_EXTRA else [])

# Převod Eurostat kód (ISO2) -> ISO3 (OWID, OxCGRT, OECD používají ISO3)
ISO2_TO_ISO3 = {
    "AT": "AUT", "BE": "BEL", "BG": "BGR", "CY": "CYP", "CZ": "CZE", "DE": "DEU",
    "DK": "DNK", "EE": "EST", "EL": "GRC", "ES": "ESP", "FI": "FIN", "FR": "FRA",
    "HR": "HRV", "HU": "HUN", "IE": "IRL", "IT": "ITA", "LT": "LTU", "LU": "LUX",
    "LV": "LVA", "MT": "MLT", "NL": "NLD", "PL": "POL", "PT": "PRT", "RO": "ROU",
    "SE": "SWE", "SI": "SVN", "SK": "SVK", "NO": "NOR", "IS": "ISL", "CH": "CHE",
}
ISO3_TO_ISO2 = {v: k for k, v in ISO2_TO_ISO3.items()}

# Hlavní města (lat, lon) – pro sluneční svit z Open-Meteo (bodová data).
CAPITALS = {
    "AT": ("Vienna", 48.21, 16.37), "BE": ("Brussels", 50.85, 4.35),
    "BG": ("Sofia", 42.70, 23.32), "CY": ("Nicosia", 35.19, 33.38),
    "CZ": ("Prague", 50.08, 14.42), "DE": ("Berlin", 52.52, 13.40),
    "DK": ("Copenhagen", 55.68, 12.57), "EE": ("Tallinn", 59.44, 24.75),
    "EL": ("Athens", 37.98, 23.73), "ES": ("Madrid", 40.42, -3.70),
    "FI": ("Helsinki", 60.17, 24.94), "FR": ("Paris", 48.86, 2.35),
    "HR": ("Zagreb", 45.81, 15.98), "HU": ("Budapest", 47.50, 19.04),
    "IE": ("Dublin", 53.35, -6.26), "IT": ("Rome", 41.90, 12.50),
    "LT": ("Vilnius", 54.69, 25.28), "LU": ("Luxembourg", 49.61, 6.13),
    "LV": ("Riga", 56.95, 24.11), "MT": ("Valletta", 35.90, 14.51),
    "NL": ("Amsterdam", 52.37, 4.90), "PL": ("Warsaw", 52.23, 21.01),
    "PT": ("Lisbon", 38.72, -9.14), "RO": ("Bucharest", 44.43, 26.10),
    "SE": ("Stockholm", 59.33, 18.07), "SI": ("Ljubljana", 46.06, 14.51),
    "SK": ("Bratislava", 48.15, 17.11), "NO": ("Oslo", 59.91, 10.75),
    "IS": ("Reykjavik", 64.15, -21.94), "CH": ("Bern", 46.95, 7.45),
}
