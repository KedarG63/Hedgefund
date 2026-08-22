# Implementation checklist

Ordered by **signal per API request**, not by how interesting the dataset is.
SEC allows 10 req/sec with no daily cap, so the constraint is throughput: a
dataset is cheap if a bulk file or a small filing count covers it.

Volumes below are measured from SEC's own quarterly filing index (2025 Q2+Q3,
619,190 filings), not estimated.

---

## Done

- [x] **Phase 1 — Foundation**
      layout, `.env`/secrets, storage verified end to end, `--dry-run`, 16 tests.
      Fixed silent data loss: both writers named files at second resolution, so
      a backfill loop overwrote its own vintages.

- [x] **Phase 2 — RBI**
      Forex reserves via the DBIE gateway (server-issued session token, not the
      client-minted one the docs assumed). Bank credit, deposits, liquidity
      operations and policy rates via fixed-URL publications. Payload cipher
      reproduced, unlocking a 491-report catalogue with history to 1911.
      Report *time series* remain behind SAP BusinessObjects.

- [x] **Phase 3 — SEC EDGAR core**
      All seven functions verified live. Point-in-time S&P 500 fundamentals
      panel: 2,318 rows, 99.4% with filed dates, universe sourced from the SPDR
      ETF's N-PORT filing (index membership is S&P IP; the ETF must disclose).

- [x] **13F — institutional positioning**
      418,332 holding rows, 28 verified filers. Weekly poll, not a quarterly
      cron: filing dates scatter across the 45-day window.

- [x] **Forms 3/4/5 — insider transactions**
      66,915 transactions from one 10 MB bulk file, replacing ~90,000 requests.
      Only 6,443 are genuine open-market buys. Cluster detection with the
      10b5-1 flag.

---

## Next: finish SEC

- [ ] **1. SCHEDULE 13D — activist stakes**  ← in progress
      67 filings/quarter for the S&P 500 (~8 seconds), 2,711 market-wide.
      Highest signal-to-cost on EDGAR. Item 4 (Purpose of Transaction) carries
      the intent: board seats, strategic alternatives, sale of the company.
      A 13G → 13D switch is itself the signal.
      *Not XBRL — needs document parsing.*

- [ ] **2. 8-K — material events**
      1,638/quarter for the S&P 500 (~3 min). Item-coded, so the classification
      is free once the item numbers are extracted. Priorities: 4.02
      (non-reliance/restatement), 5.02 (officer departure), 1.03 (bankruptcy),
      1.05 (cybersecurity), 2.02 (earnings).

- [ ] **3. `companyfacts.zip` — all XBRL, one download**
      1.4 GB replacing ~10,000 per-company calls. Unlocks custom tags
      (`nvda:DataCenterRevenue`) and full restatement history. Also lets the
      fundamentals panel widen beyond five fields at no request cost.

- [ ] **4. SCHEDULE 13G — passive 5% holders**
      1,523/quarter for the S&P 500 (~3 min). Lower signal alone, but required
      to detect the 13G → 13D transition, which is high signal.

- [ ] **5. DEF 14A — proxy statements**
      304/quarter for the S&P 500 (~37 s). Compensation structure, board
      composition, related-party transactions, say-on-pay results.

- [ ] **6. S-1 / S-3 / S-4 + 424B — dilution and deal terms**
      16 S-filings/quarter for the S&P 500. Shelf registrations predict issuance;
      S-4 carries merger terms. 424B* is high volume (15,264/qtr) so filter by
      universe first.

- [ ] **7. 20-F / 6-K — foreign issuers**
      Only if the book holds ADRs. Zero S&P 500 overlap.

- [ ] **8. N-PORT beyond SPY — fund holdings**
      13,330/quarter market-wide. Useful for flow analysis; the parser already
      exists from the S&P 500 universe work.

---

## Then

- [ ] **Phase 4 — NSE / BSE**
      Bhavcopy with delivery %, participant-wise OI (FII/DII/pro/retail), FII/DII
      cash flows, corporate announcements, quarterly results via XBRL (Indian
      taxonomy, not US GAAP). Schema-drift detection.
      *Undocumented endpoints — expect to need fresh cURL captures.*

- [ ] **Phase 5 — Commodities**
      CME settlements, MCX bhavcopy, IBJA physical rates, then the derived
      India gold premium: IBJA 999 − (COMEX × USDINR + import duty).

- [ ] **Phase 6 — Dashboard and operations**
      Streamlit on real data, data-quality tab (last fetch, row-count trend,
      silent revision detection), failure alerting, scheduled runs.

---

## Deferred / blocked

- **RBI DBIE report time series** — behind SAP BusinessObjects; `getReportLink`
  returns errorCode 10000 for anonymous callers. Publications route covers the
  same series today.
- **`submissions.zip`** — 403 at the paths tried; no verified URL.
- **DERA 13F / N-PORT data sets** — 404 at the paths tried; no verified URL.
- **MCA CHG-1 charge register** — CAPTCHA-gated, human-in-the-loop only.
  Credit-rating rationales (CRISIL/ICRA/CARE) substitute for most of the signal.

## Rules that constrain all of the above

1. Archive raw bytes before parsing; parse from the archive.
2. Stamp every parsed row with `knowledge_date` via `write_table()`.
3. Never invent an endpoint. No verified URL means ask.
4. Prefer a bulk file to N requests wherever one exists.
5. Filter by universe *before* spending requests — the filing index is free.
