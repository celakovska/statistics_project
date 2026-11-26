# statistics_project – SAN závěrečný úkol (tým I)

Vliv aktivity na sociálních sítích na duševní zdraví v evropských zemích
s počasím jako vedlejší proměnnou. Panel **27 zemí EU × roky 2011–2023**.

## Spuštění

```bash
pip install -r requirements.txt
python build_dataset.py            # stáhne a zpracuje vše (~2–5 min)
python build_dataset.py --only gbd suicide   # jen vybrané zdroje
python build_dataset.py --force    # stáhnout znovu (jinak se používá cache v data/raw)
python tests/test_offline.py       # test bez internetu na simulovaných datech
```

Stažené soubory se ukládají do `data/raw/` (cache, není v gitu), výsledky do `data/processed/`:

| Soubor | Obsah |
|---|---|
| `panel.csv` | **hlavní dataset** – země × rok, všechny proměnné |
| `<zdroj>.csv` | každý zdroj zvlášť |
| `friends_2015_2022.csv` | setkávání s přáteli (jen 2 roky, mimo panel) |
| `coverage.csv` | počet zemí s hodnotou pro každou proměnnou a rok |

Když některý zdroj selže, ostatní proběhnou dál. Soubor jde stáhnout ručně
v prohlížeči a uložit do `data/raw/` pod jménem, které program vypíše.

## Pořadí importu a proměnné

| # | Zdroj | Proměnné | Roky | Pozn. |
|---|---|---|---|---|
| 1 | Eurostat `isoc_ci_ac_i` (I_IUSNET) | `sm_total`, `sm_16_24`, `sm_16_29` (% lidí na soc. sítích) | 2011, 2013– | **2012 chybí** → `*_interp` = lineární interpolace |
| 2 | IHME GBD 2023 přes OWID | `dep_*`, `anx_*` – prevalence deprese/úzkostí v %, `*_agestd` věkově standardizovaná | 1990–2023 | modelované odhady |
| 3 | Eurostat `hlth_cd_asdr2`, `hlth_cd_acdr2` | `suicide_sdr_T/M/F` (standardizovaná úmrtnost), `suicide_cdr_15_19…` | 2011–2023 | |
| 4 | ERA5 přes OWID | `temp_mean_c`, `temp_winter_c` (XII–II), `precip_mm` | 1940– | průměr za celé území státu |
| 5 | Open-Meteo (ERA5) | `sun_hours_year`, `sun_hours_dark` (X–III), `sun_share_of_daylight` | 1940– | **bod = hlavní město** |
| 6 | Eurostat `hlth_rs_physcat` | `psy_per100k`, `*_interp`, `psy_per100k_mean` | různé | díry u některých zemí → používat průměr |
| 7 | Eurostat `nama_10_pc`, `une_rt_a` | `gdp_eur_per_cap`, `unemp_15_24_pct` | | |
| 8 | OxCGRT | `stringency_mean` (roční průměr, mimo 2020–22 = 0), `covid_period` | 2020–2022 | |
| 9 | OECD Health Statistics | `antidep_ddd` (DDD/1000 obyv./den, N06A) | | jen část zemí; při selhání API stáhnout ručně z Data Explorer jako `data/raw/oecd_antidepressants.csv` |
| 10 | Eurostat `ilc_scp09` | setkávání s rodinou/přáteli, 16–24 let | 2015, 2022 | jen pro otázku (e) |

Kódy zemí jsou podle Eurostatu (Řecko = `EL`). Nastavení zemí a let je v `src/config.py`.

## Struktura

```
build_dataset.py     # hlavní skript – postupný import a spojení
src/config.py        # země, roky, souřadnice hlavních měst
src/utils.py         # stahování s cache, parser Eurostat JSON-stat
src/loaders.py       # jedna funkce load_* na každý zdroj
tests/test_offline.py
```
