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

- [x] **Phase 4b — NSE fundamentals (XBRL)**
      `connectors/nse_fundamentals.py` turns nse_financial_results()'s XBRL
      links into structured, point-in-time income statement / balance sheet /
      cash flow facts -- the screener.in-equivalent data, built from the same
      primary filing screener parses, not from screener itself (CLAUDE.md's
      no-aggregators rule). Verified against real filings, including a
      50-company backfill: Siemens's parsed balance sheet balances exactly
      (Assets = Equity + Liabilities to the rupee); RELIANCE's Second Quarter
      AND Fourth Quarter filings both carry a full balance sheet + cash flow
      (Q1/Q3 carry neither) -- SEBI LODR Reg. 33's half-year/year-end
      disclosure rule, filed under `period="Quarterly"`
      (`relatingTo="Second Quarter"` / `"Fourth Quarter"`), NOT a separately
      labeled "Annual" period for standard April-March filers (that label
      is specific to non-April-March filers like SIEMENS). Reconcile
      "annual" against `relatingTo`, not `period`.

      Three real findings, all now regression-tested or documented: (1) a
      context's declared xbrli:period can be flatly wrong -- VST Tillers
      tags one context Oct-Dec while its own DateOfStartOfReportingPeriod
      fact says Apr-Dec; the self-reported fact, not the context, is
      authoritative. (2) pivot_table silently drops every row whose index
      column is NaN, which emptied out balance-sheet panels (all-instant, no
      period_start) until caught by running a real annual filing, not the
      synthetic test fixture. Balance sheet + cash flow both actually appear
      at HALF-YEAR (Q2) as well as year-end (Q4), not year-end alone -- an
      initial claim to the contrary, drawn from the same RELIANCE check that
      hadn't yet looked at Q2, was corrected once the dashboard's own
      balance_sheet() output showed a real Sep-2024 (Q2) column. (3)
      nse_financial_results()'s from_date/to_date
      filter is reliable ONLY for a range safely in a settled past year
      (2023: 13,147 rows) -- any range reaching toward the present degrades,
      down to 0 rows for a 2-day window. Fixed the daily run_daily.py job,
      which had shipped on that broken assumption and would have silently
      done nothing every day; it now re-scans the unfiltered index and skips
      already-archived XBRL via new_filings().

      The Nifty 50 backfill (48/50 symbols, 87,058 facts) initially looked
      like it hit a data-currency ceiling -- no window (2024, 2025,
      unfiltered) surfaced anything past toDate=2024-12-31 for ANY of the 50
      symbols. RESOLVED, not a bug: verified across the entire market-wide
      unfiltered index (3,816 rows) that NO company anywhere reports a
      quarter past 2024-12-31, despite broadCastDate (filing date) running
      through 2026-07-30 -- a late/revised refile can broadcast years after
      the period it covers (confirmed: VST Tillers' most-recent-by-broadcast
      filing, 2026-07-30, refiles its already-known Q3 FY24-25 result, first
      broadcast 2025-02-11). Sort/filter by toDate for currency, never
      broadCastDate. The backfill's ceiling IS the market's current state
      right now, not a fetch gap. HDFCLIFE and SBILIFE returned nothing in
      any window -- unexplained, separate from the currency question.

- [x] **BSE quarterly-results summary -- current data, closing the Nifty 50
      backfill's currency gap**
      `bse_quarterly_results_summary()` (connectors/nse_bse.py) pulls
      Revenue/Net Profit/EPS/Cash EPS/OPM%/NPM% straight from BSE's own
      per-company results widget, traced through its site JS after NSE's
      feed turned out stale (see above) -- `TabResults_PAR` with
      `{scripcode, tabtype:"RESULTS"}`, confirmed genuinely current against
      RELIANCE (latest column really was "Jun-26"). Wired into the
      dashboard's Fundamentals tab as an on-demand "Fetch latest from BSE"
      button, joined to the selected company via ISIN (nse_xbrl_facts ->
      bse_scrip_master). CAVEAT, caught by checking the actual numbers, not
      just the period label: this endpoint returns STANDALONE figures, not
      consolidated -- RELIANCE's Jun-26 Revenue here (Rs 1,66,013 Cr) sits in
      its standalone range, not its consolidated one, and doesn't match
      screener.in's own consolidated Rs 3,09,468 Cr for the same quarter. No
      consolidated variant found yet; flagged in both the connector
      docstring and the dashboard caption rather than presented as a match.

      Two dead ends, documented so they aren't re-attempted blind: BSE's
      `GetCorXbrlDetails_ng` (per-company structured-XBRL archive) works in
      general (verified against its Annual Reports category) but its
      "Financial Results" category returns zero rows for every company and
      date range tried. `CorpXbrlGen.aspx` (a per-announcement XBRL) turned
      out to be cover-sheet metadata only (subject/description/PDF link),
      not the underlying figures. Full current-quarter detail (matching the
      XBRL connector's granularity) remains unsolved via NSE/BSE directly
      -- see the screener.in entry immediately below for how the
      consolidated-currency gap actually got closed.

- [x] **screener.in current consolidated data -- DELIBERATE EXCEPTION to
      CLAUDE.md's no-third-party-vendors rule**
      `connectors/screener.py`. Not a default pattern: built only after the
      user was shown the direct conflict (this is literally "Option B",
      rejected earlier in the same conversation) and explicitly chose to
      override it for this one case, because no primary-source path to
      current CONSOLIDATED detail existed -- NSE's XBRL feed sticks at Q3
      FY24-25, BSE's summary endpoint is standalone only. Internal research
      use only, matching the "internal use is standard, redistribution
      needs a licence" framing CLAUDE.md already uses for NSE/BSE data.

      MECHANISM (verified live 2026-08-30 against RELIANCE/TCS/INFY, from a
      real cURL capture of the user's own logged-in "Export to Excel"
      click, not guessed): GET `/company/<SYMBOL>/consolidated/` to scrape
      a numeric company id and a fresh Django CSRF token out of the page's
      export form, then POST `/user/company/export/<id>/` with that token
      -- returns the raw .xlsx directly, no redirect. Needs
      SCREENER_CSRFTOKEN/SCREENER_SESSIONID in .env (a logged-in session,
      not an API key -- these expire; re-capture per .env.example when
      requests start 302-ing to /login/). core.http.Fetcher gained a
      `cookies` param for this.

      The "Data Sheet" tab is screener's real native export (the other
      tabs -- Profit & Loss/Quarters/Balance Sheet/Cash Flow/Customization
      -- are pre-formatted views of the same numbers). Confirmed genuinely
      current AND consolidated: RELIANCE's Quarters block includes
      Jun-2026 with Sales Rs 3,09,468 Cr -- exact match to screener's own
      page display, and the actual number the BSE standalone endpoint
      couldn't produce. Parses into the same tidy long-format shape as the
      XBRL connector (symbol/consolidated/statement/period_type/concept/
      period_end/value) so it slots into the same query patterns.

      One real landmine caught and regression-tested: the Balance Sheet's
      "Total" label appears twice per period (liabilities-side, then
      assets-side) -- a naive parse would collide or silently drop one;
      disambiguated with a numeric suffix instead. Wired into the
      dashboard's Fundamentals tab as a third "Fetch latest" button
      alongside the XBRL data and the BSE summary, each caption naming what
      the other two can't give you.

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

- [~] **Phase 6 — Dashboard and operations** *(dashboard + quality done;
      scheduling deferred while this runs on a laptop)*
      - [x] Streamlit wired to real data: 7 tabs, all 20 queries verified live.
      - [x] **Data-quality tab** — freshness from the RAW archive (not parquet:
            parsing can succeed against a stale file), row-count history per
            vintage, and silent-revision detection comparing parquet vintages on
            a declared key. Caught a real one immediately: `nse_participant_oi`
            revised because NSE ships column names with trailing whitespace.
      - [x] **Failure alerting** in `run_daily.py`, and it says so loudly when
            NO channel is configured — unconfigured alerting looks exactly like
            healthy alerting until the day it matters.
      - [ ] Scheduled runs — deferred deliberately. Cron on a laptop that sleeps
            collects a patchy archive, which is worse than an honest gap because
            it looks complete. Do this when there is a machine that stays on.

- [~] **Phase 7 — OSINT / geospatial** *(built 2026-09-02; IMD and EIA blocked
      on credentials)*
      Prompted by OSIRIS (`osirisai.live`). We integrate its SOURCE LIST, not
      the platform: embedding someone else's live map leaves no raw bytes to
      archive and no `knowledge_date`. The sources underneath are USGS, NOAA,
      NASA, IMF, EIA, USDA and the UN — primary sources in the mission's own
      sense, so this adds no vendor to the path.

      Eleven feeds verified live keyless on 2026-09-01/02 (IMF PortWatch
      chokepoints, NOAA ONI, GDELT, OpenSky, UN Comtrade, CPC precip, NVD,
      USGS, EONET, GDACS, SWPC). Three more have their endpoint shape
      confirmed but need a free key (FIRMS, USDA FAS; EIA unverified from
      this machine).

      Two findings that decide the shape. (1) The chokepoint feed is
      **validated, and Hormuz is the reason to build this phase.** An earlier
      draft called that series suspect -- 1-9 vessels/day, ~an order of
      magnitude below plausible. Wrong: the history runs 55-105/day for seven
      straight years, then breaks to 3.2 in 2026-03, matching the 2026-02-28
      escalation month by month (June MOU reopening = 12.9, July collapse =
      back to 4.9). The mass balance confirms it independently -- Bab
      el-Mandeb -45.9/day and Cape of Good Hope +46.2/day across the 2024 Red
      Sea crisis, i.e. traffic conserved onto the substitute route. The
      lesson, which generalises to every geospatial series: never validate a
      LEVEL against a remembered prior, pull the history and date the break,
      then cross-check the substitute route. Suez was not a clean baseline
      either -- its "sane" 39-48/day is itself post-2024-crisis. (2) The
      ArcGIS tenant is **shared and throttled** (429 at 6,000 units/min across
      all consumers) and lags ~9 days, so this is one archived daily pull,
      never a live dashboard query.

      SHIPPED: `connectors/chokepoints.py` (37,464 rows, 2023->2026, 28
      chokepoints), `connectors/climate.py` (ONI 918 rows/76 years; CPC daily
      India rainfall, full 2026 monsoon backfilled 91/91 days),
      `connectors/geopolitics.py` (GDELT daily + GDACS), `connectors/
      flights.py` (OpenSky OAuth2 -- decided IN, was flagged for a call),
      plus `analytics/supply_chain.py` and `analytics/monsoon.py`, 8
      run_daily jobs, 2 dashboard sub-tabs under Macro & Commodities, and 67
      offline tests.

      The reroute mass balance validates against BOTH crises: Red Sea 2024
      classifies `rerouted` (Bab el-Mandeb -36.0/day, Cape of Good Hope
      +23.6/day, 65.5% absorbed), Hormuz 2026 classifies
      `no_substitute_route` (-66.7/day, nothing absorbing). Same tool,
      opposite readings -- rerouted cargo is a freight story, destroyed cargo
      is a supply story, and they imply opposite positions.

      Three bugs the tests caught that live output did not. (1) GDELT uses
      TWO code systems in one file -- CAMEO 3-letter for actors, FIPS 10-4
      2-letter for action geography -- so one set for both made the geography
      filter dead code and **silently discarded 40.4% of the universe**
      (41,153 -> 57,795 rows/day). FIPS is not ISO: Iraq IZ, Oman MU, Russia
      RS, China CH. (2) `Fetcher`'s default 4 retries back off ~15s and
      CANNOT outlast PortWatch's 60s quota window -- raised to 8 for that
      connector, sized against the window. Nothing was lost to the failed
      backfill because raw pages archive before parsing. (3) One area-mean
      helper served two variables with different units (rain in tenths of a
      mm, gnum a count); scaling now lives at the call site.

      **IMD SOLVED 2026-09-03 WITHOUT THE API** -- the approval was the wrong
      door to wait at. IMD Pune publishes its own 0.25-degree daily gridded
      rainfall as plain annual NetCDF downloads behind an ordinary HTML form
      (POST `RF25.php`, body `RF25=<year>`, Referer required): no key, no
      registration, no approval, and it is the authoritative dataset itself,
      not a mirror. `connectors/imd.py`, read via `scipy.io.netcdf_file` (
      NetCDF-3 classic, so no netCDF4/xarray dependency). Coverage 1990-2025
      read off the form's own dropdown -- an earlier draft claimed
      "1901-present" from general knowledge, which was wrong; the dropdown is
      the authority. That still covers WMO-standard 1991-2020 in full.

      Cross-check on the full 1991-2020 climatology (30 years, 54,790 rows,
      zero missing): our independently box-averaged all-India Jun-Sep normal
      is 856.6mm against IMD's published ~868mm LPA -- within ~1.3% via a
      completely separate path, which validates the NetCDF parse, mm units,
      latitude orientation and area weighting all at once. Regional ordering
      is sane too (east_northeast wettest 1372.6mm, northwest driest
      571.9mm), which a flipped latitude axis would have inverted. Not an
      exact match and shouldn't be: our box is a rectangle, IMD's official
      figure is subdivision-weighted.

      The trap: IMD publishes annual files IN ARREARS, and posting an
      unpublished year returns HTTP 200 with a valid Content-disposition and a
      ZERO-BYTE body -- a naive caller records success and no data. So IMD
      supplies the climatology, CPC still supplies the current season, and
      the two are RECONCILED rather than mixed: `cpc_imd_bias()` measures
      mean(CPC)/mean(IMD) on overlap years over an identical window (dropping
      years where CPC coverage <90% of IMD's days, since sparse coverage
      reads as a dry bias), and `monsoon_departure()` divides the CPC
      observation by that ratio, stamping a `basis` column on every row. If
      the bias can't be measured, NO departure is reported -- an uncorrected
      CPC total against an IMD normal is two instruments read as one.

      Still blocked: **EIA** (root proves the API, but the guessed
      stocks route returns an empty body even with a bogus key where a valid
      route returns an error envelope -- so building it would be a guessed
      endpoint plus untested scaffolding, which is exactly what Phase 4 found
      broken twice). FIRMS is written but NEVER RUN and unscheduled, pending
      a key.

      REGIME DETECTION (2026-09-03) -- the phase's own lesson turned into
      code. `regime_breaks()` / `current_regime()` in analytics/
      supply_chain.py: binary segmentation on a Welch t over a deseasonalised
      series, minimum segment length so an incident cannot pass as a regime,
      NO event dates hardcoded. It independently dates the Hormuz collapse to
      2026-03-02 -- two days after the 2026-02-28 escalation -- at t=100.3,
      and finds the 2024 Red Sea crisis at Bab el-Mandeb (-54%) and Suez
      (-44%) TOGETHER WITH the mirror-image +74.9% break at Cape of Good Hope,
      i.e. it rediscovers the mass balance rather than being told it.

      Chokepoint archive deepened to 2019-01-01 (2,799 days, 78,372 rows;
      connector gained an `until` bound so a deepening backfill fetches only
      the missing window on a shared throttled tenant). The extra years
      sharpened detection measurably: Hormuz t=47.4 -> 100.3 and the dated
      break moved two days closer to the real escalation.

      THREE numbers, because one is not enough. The fixed-90-day z-score has
      a documented flaw -- once a disruption is older than the baseline it
      sits INSIDE it, so Hormuz, the largest supply event in the archive,
      scored -4.6/day. Now: `z_vs_regime` (+0.01 -- entirely normal FOR A
      CLOSED STRAIT), `regime_vs_previous_pct` (-39.9%) and `vs_baseline_pct`
      (-93.1%). The third exists because Hormuz broke TWICE, again inside its
      own closure (7.7 -> 4.6/day), so "vs previous regime" understates a
      corridor down 93% on where it started.

      One more bug of the same family as the others. Deseasonalising by
      grouping on day-of-year and subtracting the mean is wrong and hides
      itself: a day-of-year seen ONCE has residual exactly zero, so under two
      years of history the signal is annihilated and the detector silently
      finds nothing -- and even at four years it attenuates every real break
      by 1/n (~25% here) because the break's own year is inside the mean
      subtracted from it. Replaced with a two-harmonic annual fit, which
      cannot represent a step. The residual limit is genuine, not a bug:
      under ~2 years a step and an annual cycle are unidentifiable, so short
      series are left undeseasonalised deliberately. Two harmonics also fit
      ice-bound routes badly -- Bering Strait is a near-square wave and its
      breaks are flagged unreliable rather than quietly wrong.

      Rejected outright: CCTV feeds, satellite position tracking, the flight
      globe. Full verification tables, build findings and open questions in
      `PHASE7.md`.

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
