# quantdata

Primary-source data pipeline for India + US equity and commodity research.
No third-party data vendors. Every byte comes from the exchange, the regulator,
the ministry, or the broker you already pay.

Read **BUILD_GUIDE.md** first.

## Layout

```
core/storage.py       immutable raw archive + parquet warehouse + DuckDB views
core/http.py          browser-like sessions, NSE cookie priming, throttled retries
connectors/
  sec_edgar.py        US filings + XBRL facts        [complete, works today]
  nse_bse.py          India filings, results, flows, participant OI
  commodities.py      CME settlements, MCX, IBJA, CBIC gazette, DGCI&S
  rbi_dbie.py         RBI data.rbi.org.in (API discovery + fixed-URL fallback)
  mca_charges.py      CHG-1 charge register (captcha-assisted + free routes)
  stream.py           broker WebSocket -> Redis -> parquet (real-time layer)
run_daily.py          orchestration
dashboard/app.py      Streamlit single pane
```

## Quickstart

```bash
pip install -r requirements.txt
export QUANTDATA_ROOT=./data

# edit connectors/sec_edgar.py -> set EMAIL (SEC 403s without it)
python run_daily.py --job sec_daily_index
python run_daily.py
streamlit run dashboard/app.py
```

## The two rules

1. **Archive raw bytes before parsing.** Government portals overwrite themselves.
   You cannot retroactively recover a vintage. Parse from the archive, always.
2. **Stamp every row with knowledge_date.** That is the difference between a
   backtest and a fantasy.

## Legal

Exchange terms restrict bulk extraction and redistribution. Internal research
use is standard practice; anything client-facing needs an NSE/BSE licence.
MCA captcha must not be automated — see mca_charges.py for legitimate routes.
