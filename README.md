# quantdata

Primary-source data pipeline for India + US equity and commodity research.
No third-party data vendors. Every byte comes from the exchange, the regulator,
the ministry, or the broker you already pay.

Read **BUILD_GUIDE.md** for the source-by-source reasoning and **ROADMAP.md**
for what is built and what is next.

## Quickstart

Everything runs out of the project virtualenv at `.venv`. Installing into system
Python instead is the usual cause of `streamlit : The term 'streamlit' is not
recognized` — the scripts land in a directory that is not on PATH, and it can
break unrelated packages (`fastapi` and `starlette` conflict this way).

**PowerShell** (Windows):

```powershell
# one-time: create the venv and install
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# then either call the venv directly...
.\.venv\Scripts\python.exe run_daily.py --dry-run
.\.venv\Scripts\streamlit.exe run dashboard/app.py

# ...or activate it once and use short commands
.\.venv\Scripts\Activate.ps1
python run_daily.py --dry-run
streamlit run dashboard/app.py
```

**bash** (Linux/macOS/Git Bash):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run_daily.py --dry-run
.venv/bin/streamlit run dashboard/app.py
```

### Configuration

Copy `.env.example` to `.env` and fill it in. **Do not export variables by
hand** — `core/config.py` loads `.env` before anything reads the environment,
so `QUANTDATA_ROOT` and `SEC_CONTACT_EMAIL` are already set once the file
exists. (`export FOO=bar` is bash syntax and does nothing in PowerShell, where
the equivalent would be `$env:FOO = "bar"`.)

The only setting required for the US connectors is `SEC_CONTACT_EMAIL`: EDGAR
returns 403 without a contact address in the User-Agent.

### First run

```powershell
.\.venv\Scripts\python.exe run_daily.py --dry-run          # list jobs, touch nothing
.\.venv\Scripts\python.exe run_daily.py --job ibja_rates   # one job
.\.venv\Scripts\python.exe run_daily.py                    # everything due today
.\.venv\Scripts\python.exe -m pytest tests/ -q             # 211 tests
```

Then open the dashboard and start on the **Data quality** tab — the other tabs
show what was collected, that one shows whether to believe it.

## Layout

```
core/storage.py       immutable raw archive + parquet warehouse + DuckDB views
core/http.py          browser-like sessions, NSE cookie priming, throttled retries
core/config.py        .env loading, secret redaction
core/quality.py       freshness, row-count trend, silent-revision detection
core/alerting.py      failure alerts (webhook), silent when unconfigured
core/rbi_crypto.py    DBIE payload cipher

connectors/
  sec_edgar.py        US filings, XBRL frames, S&P 500 universe from N-PORT
  sec_companyfacts.py every XBRL fact ever filed, from one bulk download
  sec_13f.py          institutional positioning, 28 tracked filers
  sec_13d.py          activist stakes, Item 4 intent classified
  sec_13g.py          passive 5% holders + the 13G -> 13D transition
  sec_insider.py      Forms 3/4/5 via bulk file, cluster-buy detection
  sec_8k.py           material events, item-coded
  sec_proxy.py        pay-versus-performance from the ecd taxonomy
  nse_bse.py          India filings, results, flows, participant OI
  commodities.py      CME, MCX, IBJA + the derived India gold premium
  rbi_dbie.py         RBI gateway (session handshake + encrypted payloads)
  rbi_publications.py RBI fixed-URL publications (WSS, money market, rates)
  mca_charges.py      CHG-1 charge register (captcha-assisted + free routes)
  stream.py           broker WebSocket -> Redis -> parquet (real-time layer)

tools/                discovery scripts (RBI services, screens, reports, 13F filers)
run_daily.py          orchestration + failure alerting
dashboard/app.py      Streamlit single pane
```

## The two rules

1. **Archive raw bytes before parsing.** Government portals overwrite themselves.
   You cannot retroactively recover a vintage. Parse from the archive, always.
   This has already paid for itself: the 13G parser was wrong twice after 5,828
   documents had been fetched, and re-parsing from the archive took minutes
   instead of a 40-minute refetch.
2. **Stamp every row with knowledge_date.** That is the difference between a
   backtest and a fantasy.

## Legal

Exchange terms restrict bulk extraction and redistribution. Internal research
use is standard practice; anything client-facing needs an NSE/BSE licence.
MCA captcha must not be automated — see mca_charges.py for legitimate routes.
