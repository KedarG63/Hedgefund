# Phase 4 — NSE / BSE

Built 2026-08-22. All 13 functions in `connectors/nse_bse.py` were hit live
against production NSE/BSE endpoints and verified before being trusted —
per CLAUDE.md's rule against inventing endpoints, nothing here is guessed.

## What shipped

| Function | Source | What it is | Persisted |
|---|---|---|---|
| `nse_corporate_announcements` | NSE | Real-time announcement feed (8-K equivalent) | yes |
| `nse_financial_results` | NSE | Quarterly/annual results, XBRL + PDF links | yes |
| `nse_fii_dii` | NSE | Daily FII/DII cash-market buy/sell | no (2 rows/day, cheap to refetch) |
| `nse_option_chain` | NSE | Live option chain, OI/IV per strike | no — live/dashboard use only |
| `nse_participant_oi` | NSE | Daily FII/DII/pro/retail F&O positioning | yes |
| `nse_bhavcopy` | NSE | EOD prices, all instrument types (CM+F&O) | yes |
| `nse_bhavcopy_delivery` | NSE | EOD equities + DELIV_QTY/DELIV_PER | yes |
| `nse_bulk_deals` | NSE | Trades >0.5% equity, single broker/day | yes |
| `nse_block_deals` | NSE | Trades ≥₹25 Cr, negotiated, 100% delivery | yes |
| `nse_insider_trading` | NSE | SEBI PIT disclosures, T+2 | yes |
| `nse_shareholding_pattern` | NSE | Promoter/public split, SEBI LODR Reg. 31 | yes |
| `bse_announcements` | BSE | Corporate announcements, ~5,000 companies | yes |
| `bse_scrip_master` | BSE | Scrip code ↔ ISIN ↔ name | yes |

`run_daily.py` wires 10 of these as scheduled jobs (`nse_option_chain` and
`nse_fii_dii` are deliberately not persisted — see the table).

## Two connectors were already scaffolded and both were broken

`connectors/nse_bse.py` existed before this session (likely written from
general knowledge during Phase 1) but had never been run against live NSE/BSE.
Verifying it live surfaced two endpoints that no longer work the way the code
assumed:

**`nse_option_chain`** — `GET /api/option-chain-indices?symbol=NIFTY` now
404s outright. NSE moved to `/api/option-chain-v3`, which requires an
explicit `type` (`Indices`/`Equity`) **and** an `expiry` date string
(`"25-Aug-2026"`) — omit `expiry` and it returns `{}` with a 200, which reads
as "no data" rather than "wrong call shape". Found by fetching
`option-chain-v3.js` from the live site and reading the URL-building code
directly (the DevTools recipe in `BUILD_GUIDE.md`, done via a headless
`httpx` session instead of a browser since the goal was the JS source, not a
rendered page). Fix: call `/api/option-chain-contract-info?symbol=NIFTY`
first for the valid expiry list, then pass the first one through.

**`bse_announcements`** — pointed at `AnnGetData/w`, which is a dead path on
BSE's current site: it returns HTTP 200 with the literal JSON string
`"No Record Found!"` for every query, forever, which crashed on
`payload.get("Table")` since a string has no `.get`. The live endpoint is
`AnnSubCategoryGetData/w`, found in BSE's Angular bundle
(`main-P3QFN4I5.js`) by grepping for the `strCat`/`strSearch` param names
already in the old code. A second, non-obvious constraint: `strCat=-1` (BSE's
own "all categories" sentinel) returns `{}` once no `scrip` narrows the
query — confirmed live across several date ranges, not a formatting bug. Per
company (`scrip` given) `-1` works fine. Fix: market-wide mode sweeps a
short, individually-verified category list
(`BSE_ANNOUNCEMENT_CATEGORIES = ("Company Update", "Result", "Board Meeting",
"AGM/EGM")`) instead of asking for "all". This list is deliberately not
exhaustive — SEBI LODR defines more categories — because each addition needs
its own live check against the endpoint, not a guess from the regulation text.

## Endpoints discovered fresh this session

Six functions didn't exist in the scaffold and were found using the same
technique BUILD_GUIDE.md recommends for RBI: fetch the page, find its JS
bundle, read the fetch call.

- **`nse_bulk_deals` / `nse_block_deals`** — `bulk-block-deals-short-selling.js`
  builds `/api/historicalOR/bulk-block-short-deals?optionType=bulk_deals&from=DD-MM-YYYY&to=DD-MM-YYYY`
  (same endpoint, `optionType` switches bulk vs block).
- **`nse_insider_trading`** — `corporate-filings.js` builds
  `/api/corporates-pit-gg?index=equities` ("pit" = NSE's own shorthand for
  Prohibition of Insider Trading).
- **`nse_shareholding_pattern`** — same bundle, `/api/corporate-share-holdings-master?index=equities`.
- **`nse_bhavcopy_delivery`** — not a JS discovery; `nse_bhavcopy` was
  verified live to be missing `DELIV_QTY`/`DELIV_PER` (the UDiFF full-market
  bhavcopy dropped those columns some years back). The delivery-carrying file
  is `nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv`
  — equities only, separate from the UDiFF file, confirmed by direct fetch.

## Bugs the live testing caught that a mocked test would not have

**`nse_fii_dii` raised `UnicodeDecodeError`, not an HTTP error.** NSE serves
this endpoint `content-encoding: br` (Brotli). `httpx` only auto-decodes that
if the `brotli` package is installed; it's in `requirements.txt` but was
missing from this environment (`pip install brotli` fixed it — not a code
bug, an environment gap that a mocked/offline test suite would never surface).
`requirements.txt`'s comment now notes NSE needs it too, not just
`rbi.org.in`.

**`nse_participant_oi`'s raw CSV ships columns with trailing whitespace** —
`"Total Long Contracts      "`, `"Future Stock Short       "`. This one was
caught *by* the schema-drift check added for this phase (see below): the
check correctly flagged the columns as "missing" because a naive equality
match against the stripped name fails. Fixed by stripping column names right
after parse, same as `nse_bhavcopy_delivery`'s `" DELIV_PER"`-style headers.

Neither of these would show up in a suite that mocks the HTTP layer — both
only exist because the raw bytes from the real site were parsed for real.

## Schema-drift detection (`_check_schema`)

ROADMAP.md's Phase 4 line called for this explicitly. It's a five-line guard
in `connectors/nse_bse.py`:

```python
def _check_schema(df, expected: set, source: str, dataset: str) -> None:
    missing = expected - set(df.columns)
    if missing:
        raise RuntimeError(f"{source}.{dataset}: schema drift -- expected "
                            f"column(s) {sorted(missing)} not found. ...")
```

Applied to the four fixed-schema CSV/JSON parsers most exposed to silent
drift: `nse_bhavcopy`, `nse_bhavcopy_delivery`, `nse_participant_oi`,
`bse_scrip_master`. New columns are not an error — NSE/BSE add fields
often and that's harmless. A *missing* expected column means the site's
schema moved underneath the parser, and every downstream consumer of that
column would otherwise be silently wrong instead of loudly broken — which is
the whole point of the connector contract's "raise on failure, never return
empty" rule applied to a schema instead of a request.

## What's verified vs what's still open

**Verified live, working as documented**: all 13 functions above, run via
`run_daily.py --job <name>` against real NSE/BSE responses on 2026-08-22.
The three EOD-file jobs (`nse_bhavcopy`, `nse_participant_oi`,
`nse_bhavcopy_delivery`) correctly *raised* rather than faked success when
run against `date.today()` on a non-trading Saturday — that's the intended
behavior (rule 4 of the connector contract), not a bug; `run_daily.py`'s
`is_due()` already documents that exchange-holiday awareness belongs in the
connector, which is why these raise loudly on a missing file instead of
guessing.

**Referenced by the user's source list but not implemented — endpoint not
verified, not guessed**:
- **BSE shareholding pattern** (`Corp_Shareholding_ng`) — the exact path
  named in the reference material returned an HTML error page live, not
  JSON. Needs a fresh cURL capture from the actual BSE shareholding page
  before it's trustworthy; not added rather than shipped on a guess.
- **BSE bulk/block deals, BSE insider trading** — not attempted this phase;
  same technique (Angular bundle grep) should work, budget permitting.
- **SEBI SAST/PIT filings direct from sebi.gov.in** — different
  infrastructure (document-based, not a JSON API), out of scope for this
  phase's technique. NSE's `corporates-pit-gg` (shipped) covers the PIT side
  for NSE-listed names; SAST (5%+ acquisition, India's 13D equivalent) is not
  covered by anything in this phase.
- **`nsepython` / community wrapper libraries** — deliberately not used, per
  BUILD_GUIDE.md's own advice: useful for learning the shape, not for
  production, since they inherit NSE's undocumented-endpoint risk on top of
  their own maintenance risk.

## Tests

`tests/test_nse_bse.py` — 5 offline tests, no network, pinning the two real
bugs found live so they can't silently regress: the `_check_schema` guard
itself (including the exact trailing-whitespace shape that broke
`participant_oi`), and that `BSE_ANNOUNCEMENT_CATEGORIES` never includes the
`"-1"` sentinel that silently no-ops market-wide queries.

```bash
python -m pytest tests/test_nse_bse.py -v
```

Full suite (108 tests, excluding `test_rbi_crypto.py` which needs
`pycryptodome` — a pre-existing environment gap unrelated to this phase):

```bash
python -m pytest tests/ -v --ignore=tests/test_rbi_crypto.py
```

## Environment notes for whoever runs this next

- `pip install duckdb brotli` — both are in `requirements.txt` but were
  missing from this environment; `core/storage.py` needs the former,
  `core/http.py`'s NSE calls need the latter (silently misdecodes without it
  — see the `nse_fii_dii` bug above).
- `nse_session()` primes NSE's WAF cookies by hitting the homepage first
  (already handled in `core/http.py`); every function above goes through it.
