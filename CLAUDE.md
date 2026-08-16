# CLAUDE.md — quantdata

Persistent project instructions. Read this before every task.

## Mission

Primary-source market and macro data pipeline for a research-driven hedge fund
covering India and US equities plus commodities. Feeds quantitative research and
backtesting.

**No third-party data vendors.** Every byte comes from the exchange, the
regulator, the ministry, or the broker we already pay. If you find yourself
reaching for a data-aggregator API, stop and ask.

## The two rules that govern everything

**1. Archive raw bytes before parsing.**
Every fetch writes unchanged bytes to the raw archive with a fetch timestamp,
*then* parses from that archive. Never parse straight from a live response.

Why: government portals overwrite themselves. When a parser bug surfaces in
2028, we re-parse 2026 history. If only parsed output was kept, that history is
gone permanently and cannot be bought back.

**2. Stamp every parsed row with `knowledge_date`.**
This is what makes the data point-in-time. Without it, backtests silently use
information that did not exist at the time. `core/storage.write_table()` does
this — always go through it, never write parquet directly.

## Stack

- Python 3.12, `httpx` (not `requests` — we want HTTP/2 and connection reuse)
- pandas + pyarrow, parquet on disk
- DuckDB queries parquet directly. No server. No ETL into tables.
- Streamlit dashboard, Redis for live tick cache
- Playwright **only** for ASP.NET `__VIEWSTATE` forms and CAPTCHA-assisted flows

## Layout

```
core/storage.py       raw archive + parquet + DuckDB view registration
core/http.py          browser-like sessions, cookie priming, throttled retries
connectors/           one module per source, uniform interface
run_daily.py          orchestration
dashboard/app.py      Streamlit single pane
data/raw/             immutable, never edited, never deleted
data/parquet/         parsed output, append-only
```

## Connector contract

Every connector function must:
1. Use a session from `core.http` (never a bare `httpx.get`)
2. Call `save_raw(source, dataset, content, ext, meta)` before parsing
3. Return a DataFrame, and call `write_table()` if the data is persistent
4. Raise on failure — never return empty on error, that hides breakage
5. Carry a docstring saying what the data is and why it matters for research

## Things that will get you in trouble

**Do not invent endpoints.** Undocumented APIs change. If you do not have a
verified URL — from a cURL capture, a JS bundle, or official docs — say so and
ask. A plausible-looking wrong endpoint costs more debugging time than asking.

**Do not automate CAPTCHAs.** MCA21 is CAPTCHA-gated. The human-in-the-loop
pattern in `connectors/mca_charges.py` is the approved approach. Bypassing it
breaches terms of use and creates regulatory exposure for a fund.

**Do not commit secrets.** Session cookies, broker API keys, tokens. Everything
sensitive goes in `.env`, loaded via environment variables, and `.env` is in
`.gitignore`. Session cookies from cURL captures are credentials — treat any
that appear in chat or docs as burned.

**Do not reach for Selenium when JSON exists.** Most Indian government and
exchange sites are SPAs. The HTML is an empty shell; data arrives as JSON from a
background API. Find that API instead. Selenium on a SPA is slow, fragile, and
usually unnecessary.

**Respect rate limits.** SEC is 10 req/sec. NSE bans aggressive IPs within a
day. `core/http.Fetcher` throttles by default — do not bypass it.

## Known site-specific behaviour

**NSE** — blocks bare requests. Must load the homepage first so the WAF issues
cookies, then reuse that session. `core.http.nse_session()` handles it.

**BSE** — `api.bseindia.com` needs matching `Referer` and `Origin` headers.
Covers ~5,000 listed companies vs NSE's ~2,000. Better for smallcaps.

**RBI DBIE** — Angular SPA at `data.rbi.org.in`. Gateway pattern:
`POST /CIMS_Gateway_DBIE/GATEWAY/SERVICES/<serviceName>` with body
`{"body": {...}}`. The `authorization` header is client-generated: random prefix
+ epoch **microseconds**, not a server credential. `TS*` cookies are F5 WAF
cookies collected by hitting the homepage. Every service name is a string
literal in the JS bundles — see `discover_rbi_services.py`.

**SEC EDGAR** — requires a `User-Agent` with a contact email or returns 403.
Otherwise free, documented, no key.

## Legal

Exchange terms restrict bulk extraction and especially **redistribution**.
Internal research use is standard practice; anything client-facing needs an
NSE/BSE licence. Flag it, do not silently assume it is fine.

## Working style

- One phase at a time. Finish, verify, report, then move on.
- Write a smoke test for each connector before moving to the next.
- Prefer boring and debuggable over clever.
- If a source is unreachable from this machine, say so plainly rather than
  writing code that pretends it worked.
