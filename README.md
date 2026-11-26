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
| 2 | IHME GBD 2023 – **ruční stažení**, viz níže | `dep_agestd`, `dep_allages`, `dep_15_19`, `dep_20_24`, `dep_25_29`, totéž `anx_*` – prevalence v % | 1990–2023 | modelované odhady |
| 3 | Eurostat `hlth_cd_asdr2`, `hlth_cd_acdr2` | `suicide_sdr_T/M/F` (standardizovaná úmrtnost), `suicide_cdr_15_19…` | 2011–2023 | |
| 4 | ERA5 přes OWID | `temp_mean_c`, `temp_winter_c` (XII–II), `precip_mm` | 1940– | průměr za celé území státu |
| 5 | Open-Meteo (ERA5) | `sun_hours_year`, `sun_hours_dark` (X–III), `sun_share_of_daylight` | 1940– | **bod = hlavní město** |
| 6 | Eurostat `hlth_rs_physcat` | `psy_per100k`, `*_interp`, `psy_per100k_mean` | různé | díry u některých zemí → používat průměr |
| 7 | Eurostat `nama_10_pc`, `une_rt_a` | `gdp_eur_per_cap`, `unemp_15_24_pct` | | |
| 8 | OxCGRT | `stringency_mean` (roční průměr, mimo 2020–22 = 0), `covid_period` | 2020–2022 | |
| 9 | OECD Health Statistics | `antidep_ddd` (DDD/1000 obyv./den, N06A) | | jen část zemí; při selhání API stáhnout ručně z Data Explorer jako `data/raw/oecd_antidepressants.csv` |
| 10 | Eurostat `ilc_scp09` | setkávání s rodinou/přáteli, 16–24 let | 2015, 2022 | jen pro otázku (e) |

### Ruční stažení GBD (krok 2)

OWID data GBD 2023 označuje jako *non-redistributable* (CSV vrací 403) a IHME
vyžaduje bezplatný účet, takže je nelze stáhnout automaticky:

1. https://vizhub.healthdata.org/gbd-results/ – přihlásit se (registrace zdarma)
2. GBD Estimate: *Cause of death or injury*, Measure: *Prevalence*, Metric: **Rate**,
   Cause: *Depressive disorders* + *Anxiety disorders*, Location: 27 zemí EU,
   Age: *Age-standardized*, *All ages*, *15-19*, *20-24*, *25-29 years*, Sex: *Both*, Year: 2011–2023
3. Download → CSV, ZIP (nebo rozbalené CSV) uložit do `data/raw/ihme_gbd/`
4. `python build_dataset.py --only gbd`, pak celé `python build_dataset.py`

Metric *Rate* (na 100 000) se přepočítá na % (/1000). *Percent* v GBD znamená podíl
na všech příčinách, ne prevalenci, proto se nepoužívá.

Kódy zemí jsou podle Eurostatu (Řecko = `EL`). Nastavení zemí a let je v `src/config.py`.

## Struktura

```
build_dataset.py     # hlavní skript – postupný import a spojení
src/config.py        # země, roky, souřadnice hlavních měst
src/utils.py         # stahování s cache, parser Eurostat JSON-stat
src/loaders.py       # jedna funkce load_* na každý zdroj
tests/test_offline.py
```
