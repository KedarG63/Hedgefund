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

- [x] **Phase 4 — NSE / BSE**
      13 connector functions, all verified live 2026-08-22. Bhavcopy (all
      instrument types) + a separate delivery-% file (the UDiFF bhavcopy
      dropped DELIV_QTY/DELIV_PER years ago), participant-wise OI, FII/DII
      cash flows, corporate announcements + quarterly results XBRL, bulk/block
      deals, insider trading (PIT), shareholding pattern, BSE announcements +
      scrip master. Schema-drift guard (`_check_schema`) catches column
      renames/drops before they reach a backtest silently. Two previously-
      scaffolded endpoints turned out broken on live NSE (option-chain,
      BSE announcements) and were fixed against fresh JS-bundle captures, not
      guessed. Full trail in `PHASE4.md`.

---

## Next: finish SEC

- [x] **1. SCHEDULE 13D — activist stakes**
      1,393 filings for 2025Q3 (278 new, 1,115 amendments), 5,650 reporting
      persons, zero parse failures. Structured XML since the 2024 rules, so no
      HTML scraping. Item 4 intent classified into six flags, full text retained.
      Item 4 is present on 100% of originals but absent from 34% of amendments
      (unchanged items are incorporated by reference) — a null there means
      "unknown", never "passive".

- [x] **2. 8-K — material events**
      122,144 (filing, item) rows over 57,927 filings and 492 companies,
      spanning 2006–2026, from 492 requests in under 6 minutes. Item codes come
      from the submissions API, so it is ONE REQUEST PER COMPANY rather than per
      filing — the same lesson as the insider bulk file. 425 critical-tier
      events. Amendment lag is separated from filing lag: an 8-K/A carries the
      original event's reportDate, and one showed a 947-day "delay".

- [x] **3. `companyfacts.zip` — all XBRL, one download**
      1.41 GB in 6.3 min, 20,266 companies, replacing ~10,000 per-company calls.
      Streamed to disk so it never sits in memory. Yields 237,995 genuine
      revisions across 491 S&P 500 companies in 3.9 min.
      Two corrections to the source report: companyfacts contains **no**
      company-defined extension tags (ten SEC-standard prefixes only, sampled
      across 400 companies), and the largest "restatements" are mostly *tagging
      corrections* plus scale rescalings, which are flagged separately.

- [x] **4. SCHEDULE 13G — passive 5% holders**
      5,853 filings for 2025Q3, 13,536 reporting persons, zero parse failures.
      Filing rule captured: 4,049 institutional (13d-1(b)), 1,328 passive
      (13d-1(c)), 419 exempt. Yields **14 confirmed 13G → 13D transitions**.
      Different schema from 13D despite the family resemblance — lowercase
      namespace, `classPercent` not `percentOfClass`, and `issuerCik` not
      `issuerCIK`, which is case-sensitive and silently zeroed the whole signal
      until caught.

- [x] **5. DEF 14A — proxy statements**
      Pay-versus-performance comes FREE from the companyfacts archive: SEC tags
      it under the `ecd` taxonomy, so CEO pay actually paid, disclosed pay,
      company TSR and peer TSR need no per-filing fetch at all. 5,494 facts,
      164 companies, FY2021–2026, plus 812 company-years of derived pay-vs-TSR.
      Corpus index: 11,502 proxy filings across 489 companies, 2007–2026.
      Coverage is 34% because many filers tag the pay table *dimensionally* and
      companyfacts keeps only non-dimensional facts — a large free sample, not a
      census. Board composition and related-party transactions are untagged
      anywhere and still need HTML parsing.

- [ ] **6. S-1 / S-3 / S-4 + 424B — dilution and deal terms**  ← next
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

- [x] **Phase 5 — Commodities**
      CME settlements, MCX bhavcopy, IBJA physical rates, and the derived
      **India gold premium**: IBJA 999 − (COMEX × USDINR, converted to ₹/10g and
      grossed up by duty and GST). Computes at **AM 2.63% / PM 3.35%** on
      2026-08-21 from four legs we own end to end.
      Two scaffolded endpoints had drifted and were fixed by reading the sites'
      own JavaScript: CME now *requires* `tradeDate`, and MCX's old path returns
      HTTP 200 with a 404 page body. CBIC is deliberately not wired — its index
      returns a 3 KB shell, so the duty is a documented parameter instead of a
      guess.

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
6. **Deduplicate the filing index by accession before fetching.** `master.idx`
   lists a filing once per ASSOCIATED CIK, so ownership forms appear twice: once
   under the reporting person, once under the subject issuer.

   | form | raw index rows/qtr | distinct | duplicated |
   |---|---|---|---|
   | Forms 3/4/5 | 88,708 | 42,484 | 52% |
   | SCHEDULE 13D | 2,711 | 1,355 | 50% |
   | SCHEDULE 13G | 12,037 | 6,018 | 50% |
   | DEF 14A | 1,790 | 1,659 | 7% |
   | 8-K | 17,423 | 17,040 | 2% |
   | 13F, N-PORT | — | — | 0% |

   Company-filed forms are barely affected; ownership forms halve.
7. Reuse one HTTP session across a loop. Building a client per request discards
   the connection pool and cost 2.4x throughput.
