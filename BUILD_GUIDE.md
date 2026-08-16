# Build Guide — Getting Real Data In, Fast

Plain-language answers to your five questions, with the working code in this repo.

---

## The one technique that replaces 90% of Selenium

Before anything else, learn this. It will save you months.

Almost every modern site — RBI DBIE, NSE, BSE, MCX — is a **single-page app**. The HTML you get is an empty shell. The actual data arrives afterwards, as JSON, from a background API call.

I verified this for you: fetching `data.rbi.org.in` returns a near-empty page with just a title tag. Nothing else. So Selenium would be scraping a blank page and waiting for JavaScript — slow, fragile, memory-hungry.

**Instead, call the JSON API the app itself calls:**

1. Open the site in Chrome
2. Press **F12** → **Network** tab → filter **Fetch/XHR**
3. Click through to the data you want, by hand, once
4. Watch the XHR requests appear. Click one → **Response** tab. That's your data, already structured.
5. Right-click the request → **Copy** → **Copy as cURL**
6. Paste into **curlconverter.com** → get working Python
7. Drop it into a connector

Ten minutes per source, and you get JSON instead of HTML soup, at roughly 50x the speed, with far less breakage.

**Bonus trick for RBI specifically:** RBI ships an official *RBIDATA* mobile app. Mobile apps talk to clean, versioned JSON APIs — usually better structured than the web ones. Run the app through mitmproxy or Charles Proxy for an hour and you'll have the whole API surface mapped.

**When Selenium/Playwright genuinely is the right tool:** ASP.NET forms with `__VIEWSTATE` round-tripping (DGCI&S Tradestat is the classic), and CAPTCHA-assisted workflows. That's it. Two cases.

---

## 1. CHG-1 Charge Register — the hidden leverage map

### Why you're right that this matters

When an Indian company pledges an asset for a secured loan, it must file **Form CHG-1** with the Registrar of Companies. The resulting "Index of Charges" shows: which lender, how much, created when, modified when, satisfied when.

Four reasons it beats financial statements:

1. **It covers unlisted subsidiaries.** A listed parent can look clean on a consolidated basis while an unlisted subsidiary quietly pledges assets. Charges appear here months before any statement.
2. **A new charge is a bank's private credit judgement.** Someone did diligence and lent. Early satisfaction is deleveraging. A cluster of modifications is usually refinancing under stress.
3. **Charge holder identity** lets you rebuild the private-credit network graph of corporate India — which lender is exposed to whom.
4. **Statutory deadline, not quarterly cadence.** Far more timely.

### The access reality

Index of Charges is **free** on the MCA V3 portal, no login. Only document downloads cost money (~₹100 each). But it's **CAPTCHA-gated**.

I won't help you defeat the CAPTCHA. Not out of squeamishness — it explicitly breaches MCA's terms, and for a fund the downside isn't a blocked IP, it's a regulatory conversation. It's also unnecessary, for a reason that turns out to be an advantage:

**The universe is small enough to do by hand.** NIFTY 500 plus material subsidiaries is roughly 1,500–2,000 CINs. At ~20 seconds each with the assisted workflow below, that's one focused day, then a couple of hours per quarter to refresh.

And here's the thing — the CAPTCHA *is why this dataset is underexploited*. If it were scrapeable, every quant shop in Mumbai would already have it. The friction is the moat. One day of manual work buys you a dataset your competitors don't have.

### The assisted pattern (in `connectors/mca_charges.py`)

A Playwright script that opens the page, pre-fills the CIN from your queue, pauses for you to type the CAPTCHA, then auto-scrapes the table, archives the raw HTML, and advances to the next CIN. You're the CAPTCHA solver; the bot does the other 95%.

```bash
pip install playwright && playwright install chromium
python -m connectors.mca_charges
```

### Four CAPTCHA-free routes — start here, they're faster

| Route | What you get | Effort |
|---|---|---|
| **Credit rating rationales** (CRISIL, ICRA, CARE, India Ratings) | Every facility, limit and lender, itemised. Updated on each rating action — often *more current* than MCA. Free, fully scrapeable. | **Best substitute. Start here.** |
| **Annual report notes** on secured borrowings | Lender, collateral, amount for listed names. PDFs on IR pages. | pdfplumber, ~80% of the signal |
| **CERSAI** (cersai.org.in) | Central security-interest registry — property and receivables | Separate public search |
| **NCLT / DRT / IBBI orders** | When a charge goes bad it becomes a court document. Free, full-text, names every lender. | Distress signal, high value |

Practical sequencing: build the rating-rationale scraper first (a week, gets you most of the signal), then do the MCA manual pass for the names where the rationale is thin or the subsidiary structure is opaque.

---

## 2. Gold from four sources → one dashboard

### The architecture, in one line

```
fetch → raw archive (immutable) → parse → parquet → DuckDB views → Streamlit
                                                  ↘ Redis (live ticks)
```

### Why these four sources beat the LBMA benchmark

You don't just get a price — you get **spreads that are themselves signals**:

- **COMEX** (CME settlement, free daily) — the global benchmark
- **MCX** (India futures, free bhavcopy) — India's exchange-traded truth
- **IBJA** (India physical, AM/PM rates by purity) — what physical actually clears at. RBI struck Sovereign Gold Bond issue prices off IBJA 999 averages.
- **CBIC notified tariff values** (Gazette, fortnightly) — the Government of India's own official customs valuation

**IBJA minus (COMEX × USDINR + import duty)** = the India physical premium/discount. It widens on import restrictions, festival demand, and shifts in the smuggling channel. That spread is not available anywhere as a product. You compute it because you own all four legs.

**DGCI&S trade data** (HS code 7108 for gold) gives you the import *volume* — the demand side underneath the price.

### The storage rule that makes it all work

Every byte you fetch goes to the raw archive, unchanged, with a fetch timestamp, **before** anything parses it. You parse from the archive, never from the live web.

Why this is non-negotiable: when you find a parser bug in 2028 — and you will — you re-parse 2026 history. If you only kept parsed output, that history is gone. Government portals overwrite themselves; there's no going back.

`core/storage.py` does this automatically. Every parsed row also gets stamped with `knowledge_date`, which is what makes your data genuinely point-in-time.

### Why DuckDB + parquet, not Postgres

Your data is append-only analytical time series. DuckDB queries parquet files directly — no server to run, no ETL step, and it beats Postgres on this workload. `register_views()` exposes every dataset as a SQL view automatically, so you can join RBI macro against NSE prices against CME settlements in one query. Move to ClickHouse only when you outgrow a single machine.

### Run it

```bash
pip install -r requirements.txt
python run_daily.py                    # ingest everything
streamlit run dashboard/app.py         # single pane
```

---

## 3. RBI DBIE — scrape it or not?

**Neither, quite.** Two paths, and you want both.

**Path A — the SPA's JSON API.** Use the DevTools recipe above. Ten minutes of clicking gives you the endpoints for whichever series you care about. Fast, structured, and this is where the near-real-time data lives. `connectors/rbi_dbie.py` has the scaffold; fill in `ENDPOINTS` after discovery.

**Path B — fixed-URL publications, zero reverse engineering.** These never move and are officially published on a known schedule:

| Publication | Cadence | What's in it |
|---|---|---|
| **Weekly Statistical Supplement** | Every Friday | Forex reserves, money supply, bank credit & deposits — RBI's highest-frequency release |
| **Monthly Bulletin** | Monthly | Contains *State of the Economy* — RBI's own nowcast of e-way bills, power, fuel, tolls, UPI |
| **Handbook of Statistics** | Annual | Enormous. **Each edition is a frozen snapshot — collecting every edition gives you revision history**, which is the closest thing to a point-in-time archive you can retroactively build |
| **Press releases** | Daily | Money market operations, auction results |

Path B is slower but bulletproof. Use it as the backstop that keeps running when the SPA changes its API — which it will.

**What to prioritise from DBIE** for actionable signals: systemic liquidity (daily LAF), sectoral deployment of bank credit (monthly — tells you which industries banks are actually funding), forex reserves (weekly), and the OIS/policy rate history for building your policy-surprise series.

---

## 4. SEC EDGAR XBRL — yes, fully free from India

Correct, and it's the easiest primary source on earth. No key, no registration, no geo-restriction. The only requirement is a User-Agent header with your email — omit it and you get a 403.

`connectors/sec_edgar.py` is complete and working. Four endpoints do almost everything:

| Function | What it gives you |
|---|---|
| `company_facts(cik)` | **Every XBRL fact a company ever filed**, in one JSON, each with a `filed` date — that field is what makes it point-in-time |
| `frames("Revenues", "CY2025Q1")` | **One concept, one period, every US filer at once.** This is how you build a fundamentals panel in minutes instead of weeks. Most people miss this endpoint. |
| `submissions(cik)` | Every filing with accession numbers and dates |
| `daily_index(date)` | Every filing made market-wide on a given day — poll nightly for a complete filings feed |

Rate limit is 10 requests/second. The session in `core/http.py` already throttles to stay under it.

---

## 5. Indian quarterly fundamentals — ranked by reliability

Your list is right; here's the ordering I'd use, best first.

**1. Exchange XBRL — the actual primary source.** `nse_financial_results()` returns rows that each carry an XBRL link *and* a PDF link. **Take the XBRL.** It's machine-readable, needs no OCR, and it's the filing itself rather than someone's rendering of it. Parse with `arelle` — note the Indian taxonomy differs from US GAAP, so you need separate mappings.

**2. BSE announcements API** — friendlier than NSE's, and covers ~5,000 listed companies against NSE's ~2,000. **For smallcap coverage, BSE is the better feed.** Most people default to NSE and silently lose half the universe.

**3. `nsepython` / `nse_results()`** — fine for prototyping. But it's a community wrapper over the same undocumented endpoints, so you inherit its maintenance risk on top of NSE's. Use it to learn the shape, then write your own connector so you control the failure modes.

**4. Screener.in** — genuinely clean tables, and useful for cross-checking. But it's a derived source with someone else's standardisation choices baked in, and scraping it at volume is against their terms. Use it to validate your pipeline, not to feed it.

**Skip Selenium here entirely.** Both exchanges expose JSON. `connectors/nse_bse.py` handles the session priming NSE requires — you must load the homepage first so the WAF hands you cookies, then reuse that session.

**The one you should grab immediately:** `nse_participant_oi()` — daily FII / DII / proprietary / retail long-short open interest across index futures, index options, stock futures and stock options. No other major market publishes institutional positioning for free, daily. Treat it as a crowding factor rather than a directional signal.

---

## 6. Real-time commodities — the honest answer

**There is no free real-time exchange feed.** Every exchange sells its feed; anyone offering it free is redistributing without a licence, and building a fund on that is a bad foundation.

**But you're solving a problem you don't have.** You'll have a broker anyway — and your broker's API *is* a licensed real-time feed, included in the relationship, for roughly the price of lunch.

| Market | Route | Cost | Coverage |
|---|---|---|---|
| India — NSE, BSE, **MCX** | **Zerodha Kite Connect** | ~₹2,000/mo | WebSocket ticks, 3,000 instruments. **MCX gives you gold, silver, crude, natgas, copper, zinc, cotton, CPO** |
| India — alternatives | Dhan, Upstox, Fyers, Angel One SmartAPI | similar | same shape |
| US / global | Interactive Brokers API | free with account | equities, futures, FX |
| Exchange-direct, no broker | Databento, Polygon.io | paid | US equities/options/futures |

I notice you already have a Kite connector available — that's your India real-time layer, ready to go.

### The two-layer pattern

```
broker WebSocket → Redis (hot: last-value + pub/sub) → dashboard live tab
                 → ring buffer → parquet every 60s (cold: permanent history)

exchange EOD files (MCX bhavcopy, CME settlements) → authoritative settlement
```

Reconcile nightly. **When they diverge, trust the exchange file** — settlement is what contracts actually mark against.

Never write every tick straight to disk. You'll melt the disk and the dashboard will crawl. `connectors/stream.py` implements the buffered pattern.

### Agri and food commodities

- **Agmarknet** (agmarknet.gov.in) — daily arrivals and prices by *mandi* and commodity across India. Enormous, messy, and a direct read on food inflation weeks ahead of CPI. Nobody mines this properly.
- **NCDEX** — agri futures bhavcopy
- **USDA** WASDE + NASS APIs — global agri, free and documented
- **FCI** — foodgrain stock position

---

## Suggested first 30 days

| Days | Do this |
|---|---|
| 1–3 | Stand up `core/storage.py`, start the daily raw archive. **Do this before writing a single parser** — every day you delay is vintage history permanently lost. |
| 4–7 | SEC EDGAR (works immediately, no reverse engineering, builds confidence) |
| 8–14 | NSE/BSE: bhavcopy, announcements, results XBRL, FII/DII, participant OI |
| 15–20 | Commodities: CME settlements, MCX bhavcopy, IBJA. Get the gold spread computing. |
| 21–25 | RBI: DevTools discovery session, plus the fixed-URL publication fallbacks |
| 26–30 | Broker API for live ticks; wire the dashboard; **build the alerting** |

**On that last point:** a job that silently stops running looks exactly like a market with no news. Alert on every failure and on any series whose *history changes* between fetches. Silent breakage kills more scraped pipelines than any other cause.

---

*Everything here is data engineering, not investment advice — I'm not a licensed advisor. Two legal flags worth taking seriously: exchange terms restrict bulk extraction and especially **redistribution** (internal research is standard practice; anything client-facing needs an NSE/BSE licence), and the MCA CAPTCHA point above. Both are worth ten minutes with counsel before you scale, because the exposure sits with the fund.*
