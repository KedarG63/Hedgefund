"""
Daily orchestration. Start with this; graduate to Dagster/Prefect when you have
30+ jobs and need dependency graphs and backfills.

    python run_daily.py                    # run everything due today
    python run_daily.py --all              # run everything, ignore schedule
    python run_daily.py --job nse_bhavcopy
    python run_daily.py --dry-run          # list what WOULD run, touch nothing
"""
import argparse
import traceback
from datetime import date, timedelta

import pandas as pd

import core  # noqa: F401  -- loads .env before any connector reads os.environ
from core.config import describe

JOBS = {}


def job(name, schedule="daily", desc=""):
    def deco(fn):
        JOBS[name] = {"fn": fn, "schedule": schedule, "desc": desc}
        return fn
    return deco


def is_due(schedule: str, on: date) -> bool:
    """
    Whether a schedule fires on a given date. Deliberately crude -- calendar
    awareness (exchange holidays) belongs in the connector, which knows whether
    a missing file means 'holiday' or 'we are broken'.
    """
    if schedule == "daily":
        return True
    if schedule == "weekdays":
        return on.weekday() < 5
    if schedule.startswith("weekly_"):
        want = schedule.split("_", 1)[1].lower()
        days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        return want in days and on.weekday() == days.index(want)
    if schedule == "monthly":
        return on.day == 1
    return True


# ---------------------------------------------------------------- India EOD
@job("nse_bhavcopy", schedule="weekdays", desc="NSE EOD prices, all instrument types (CM + F&O)")
def _nse_bhavcopy():
    from connectors.nse_bse import nse_bhavcopy
    return nse_bhavcopy(date.today())


@job("nse_participant_oi", schedule="weekdays",
     desc="FII/DII/pro/retail open interest -- institutional positioning")
def _participant_oi():
    from connectors.nse_bse import nse_participant_oi
    return nse_participant_oi(date.today())


@job("upstox_fii", desc="FII positioning by segment via Upstox (broker-licensed)")
def _upstox_fii():
    # Cross-validates nse_participant_oi: both reported FII index futures at
    # 26,060 long / 235,915 short for 2026-08-21, from entirely separate paths.
    from connectors.upstox import fii_activity
    return fii_activity(interval="1D")


@job("nse_fii_dii", desc="FII/DII cash market flows")
def _fii_dii():
    from connectors.nse_bse import nse_fii_dii
    return nse_fii_dii()


@job("nse_results", desc="Quarterly results index (XBRL attachments)")
def _results():
    from connectors.nse_bse import nse_financial_results
    return nse_financial_results("Quarterly")


@job("nse_fundamentals", desc="Structured income statement/balance sheet/cash flow -- "
     "re-scans NSE's unfiltered results index and parses whatever XBRL isn't already "
     "archived; registered AFTER nse_results since it re-derives the same call rather "
     "than depending on that job's output")
def _nse_fundamentals():
    """
    Deliberately NOT a from_date/to_date trailing window: verified live
    2026-08-30 that nse_financial_results()'s date filter collapses to
    near-zero rows for any range reaching into the current year (see that
    function's docstring) -- a 2-day window returned 0 rows outright, a
    silent no-op that would have looked like a healthy daily run forever.
    The unfiltered call is the only signal proven to carry genuinely recent
    filings, so this re-scans it every day and lets new_filings() do the
    actual "what's new" filtering via the raw archive (already-seen XBRL
    URLs are find_raw() hits, not re-fetched) -- more requests than a true
    incremental window would need, but correct instead of silently empty.
    """
    from connectors.nse_bse import nse_financial_results
    from connectors.nse_fundamentals import financial_facts, new_filings
    filings = pd.concat([
        nse_financial_results("Quarterly"),
        nse_financial_results("Annual"),
    ], ignore_index=True)
    filings = new_filings(filings)
    if filings.empty:
        return None  # nothing new since the last run -- not a failure
    return financial_facts(filings)


@job("nse_bhavcopy_delivery", desc="NSE delivery % -- speculative vs genuine volume")
def _nse_bhavcopy_delivery():
    from connectors.nse_bse import nse_bhavcopy_delivery
    return nse_bhavcopy_delivery(date.today())


@job("nse_bulk_block_deals", desc="NSE bulk (>0.5% equity) and block (>=Rs25Cr) deals")
def _nse_bulk_block():
    from connectors.nse_bse import nse_bulk_deals, nse_block_deals
    yday, today = date.today() - timedelta(days=1), date.today()
    return {"bulk": nse_bulk_deals(yday, today), "block": nse_block_deals(yday, today)}


@job("nse_insider_trading", desc="SEBI PIT insider disclosures, T+2")
def _nse_insider():
    from connectors.nse_bse import nse_insider_trading
    return nse_insider_trading()


@job("nse_shareholding_pattern", schedule="weekly_monday",
     desc="Promoter/public shareholding split, SEBI LODR Reg. 31")
def _nse_shareholding():
    from connectors.nse_bse import nse_shareholding_pattern
    return nse_shareholding_pattern()


@job("bse_announcements", desc="BSE corporate announcements (~5k companies)")
def _bse_ann():
    from connectors.nse_bse import bse_announcements
    return bse_announcements(date.today() - timedelta(days=1), date.today())


@job("bse_scrip_master", schedule="weekly_monday", desc="BSE scrip code <-> ISIN <-> name master")
def _bse_scrip_master():
    from connectors.nse_bse import bse_scrip_master
    return bse_scrip_master()


@job("nse_corporate_announcements", desc="NSE corporate announcements, trailing 24h window -- "
     "NOT the 60s-poll real-time feed the connector's docstring describes; see "
     "analytics/corporate_events.py for why that's out of scope for the daily batch")
def _nse_corp_announcements():
    from connectors.nse_bse import nse_corporate_announcements
    yday, today = date.today() - timedelta(days=1), date.today()
    return nse_corporate_announcements(frm=yday.strftime("%d-%m-%Y"), to=today.strftime("%d-%m-%Y"))


# ---------------------------------------------------------------- India F&O (options)
@job("nse_option_chain", schedule="weekdays",
     desc="NIFTY/BANKNIFTY option chain snapshot, flattened and persisted "
          "(nse_option_chain() already fetched this live -- was never parsed to parquet)")
def _nse_optchain():
    from analytics.options import capture_and_persist_option_chain
    return capture_and_persist_option_chain(symbols=("NIFTY", "BANKNIFTY"))


# ---------------------------------------------------------------- India credit ratings
@job("crisil_rating_actions", desc="CRISIL rating-action feed (upgrades/downgrades/"
     "assignments/issuer-not-cooperating) -- the free substitute for the CAPTCHA-gated "
     "MCA charge register named in connectors/mca_charges.py")
def _crisil_rating_actions():
    from connectors.credit_ratings import crisil_rating_actions
    return crisil_rating_actions(days_back=7)


@job("icra_rating_actions", desc="ICRA rating-action feed, same shape as CRISIL's -- "
     "ICRA additionally exposes lender-wise bank facilities as a structured table "
     "(icra_bank_facilities(), not called from this daily job -- it's per-company, "
     "not part of the market-wide feed)")
def _icra_rating_actions():
    from connectors.credit_ratings import icra_rating_actions
    return icra_rating_actions(days_back=7)


# ---------------------------------------------------------------- Quant Signal Engine
# Every job below is DERIVED: it reads already-ingested views, computes, and
# writes back under source="derived". Registered after the India EOD section
# (nse_bhavcopy, nse_participant_oi, nse_bulk_block_deals, nse_insider_trading,
# nse_shareholding_pattern, nse_option_chain all precede these by registration
# order) so every input each job needs already exists.
@job("analytics_capm_beta", schedule="weekdays",
     desc="DERIVED: OLS beta/alpha vs NIFTYBEES, trailing window")
def _analytics_capm_beta():
    from analytics.capm import compute_betas
    return compute_betas()


@job("analytics_correlation", schedule="weekdays",
     desc="DERIVED: rolling pairwise correlation + cointegration-screened pairs shortlist")
def _analytics_correlation():
    from analytics.correlation import pairs_screen, rolling_correlation
    rolling_correlation()
    return pairs_screen()


@job("analytics_momentum", schedule="weekdays",
     desc="DERIVED: cross-sectional momentum z-score + variance-ratio diagnostic")
def _analytics_momentum():
    from analytics.momentum import momentum_zscore, variance_ratio_test
    momentum_zscore()
    return variance_ratio_test()


@job("analytics_risk_model", schedule="weekdays",
     desc="DERIVED: momentum/size/low-vol cross-sectional factor model + composite score -- "
          "registered AFTER analytics_momentum (reads its persisted output) and "
          "bse_scrip_master (size factor's market-cap join)")
def _analytics_risk_model():
    from analytics.risk_model import build_factor_model
    return build_factor_model()


@job("analytics_option_greeks", schedule="weekdays",
     desc="DERIVED: Black-Scholes IV/Greeks off the option-chain snapshot -- "
          "registered AFTER nse_option_chain")
def _analytics_option_greeks():
    from analytics.options import compute_greeks_for_chain
    return compute_greeks_for_chain()


@job("analytics_events", schedule="weekdays",
     desc="DERIVED: insider-filing frequency, bulk/block-deal anomaly, "
          "FII-DII divergence, promoter-holding change")
def _analytics_events():
    from analytics.events import (
        bulk_block_deal_anomaly, fii_dii_divergence,
        insider_filing_frequency, promoter_holding_change,
    )
    bulk_block_deal_anomaly()
    fii_dii_divergence()
    promoter_holding_change()
    return insider_filing_frequency()


@job("analytics_corporate_events", schedule="weekdays",
     desc="DERIVED: BSE material-event flags (mgmt change/resignation/credit "
          "action/scheme of arrangement) -- registered AFTER bse_announcements "
          "and bse_scrip_master")
def _analytics_corporate_events():
    from analytics.corporate_events import material_events
    return material_events()


@job("analytics_fii_cross_source", schedule="weekdays",
     desc="DERIVED: NSE participant-OI vs Upstox FII activity reconciliation "
          "-- flags a mismatch between two feeds that should read the same")
def _analytics_fii_cross_source():
    from analytics.fii_divergence import fii_source_divergence
    return fii_source_divergence()


@job("analytics_outliers", schedule="weekdays",
     desc="DERIVED: IsolationForest multivariate outlier flag across beta/momentum/"
          "bulk-deal/promoter-change -- registered AFTER analytics_events")
def _analytics_outliers():
    from analytics.outliers import multivariate_outliers
    return multivariate_outliers()


@job("analytics_digest", schedule="weekdays",
     desc="DERIVED: DeepSeek terse synthesis -- registered LAST, after every "
          "other analytics job it reads from")
def _analytics_digest():
    from analytics.digest import run_digest
    return run_digest()


# ---------------------------------------------------------------- Commodities
@job("mcx_bhavcopy", desc="MCX futures settlements -- India commodity leg")
def _mcx():
    from connectors.commodities import mcx_bhavcopy
    return mcx_bhavcopy(date.today())


@job("ibja_rates", desc="IBJA physical gold/silver rates by purity")
def _ibja():
    from connectors.commodities import ibja_rates
    return ibja_rates()


@job("cme_settlements", schedule="weekdays",
     desc="CME/COMEX settlements -- global benchmark leg of the gold premium")
def _cme():
    from connectors.commodities import cme_settlements, CME_PRODUCTS
    # tradeDate is REQUIRED by the service; a non-trading day returns an empty
    # settlement list rather than an error, so empty means "no session".
    return {p: cme_settlements(p, date.today()) for p in CME_PRODUCTS}


@job("india_gold_premium", schedule="weekdays",
     desc="DERIVED: IBJA 999 minus landed COMEX cost -- registered AFTER its three legs")
def _gold_premium():
    # Must run after ibja_rates, cme_settlements and rbi_key_indicators, which
    # is why it is registered last among the commodity jobs: run_daily executes
    # in registration order.
    from connectors.commodities import india_gold_premium
    return india_gold_premium()


# ---------------------------------------------------------------- US
@job("sec_sp500_constituents", schedule="weekly_monday",
     desc="S&P 500 membership + weights from the SPDR ETF's N-PORT filing")
def _sec_sp500():
    from connectors.sec_edgar import sp500_constituents
    return sp500_constituents()


@job("sec_us_fundamentals", schedule="monthly",
     desc="Point-in-time S&P 500 fundamentals panel (revenue, NI, assets, equity, shares)")
def _sec_fundamentals():
    from connectors.sec_edgar import fundamentals_panel, latest_completed_quarter
    return fundamentals_panel(latest_completed_quarter())


@job("sec_proxy", schedule="monthly",
     desc="Proxy corpus + pay-versus-performance (CEO pay actually paid vs TSR)")
def _sec_proxy():
    # Monthly: proxies are annual, and the pay data comes free from the
    # companyfacts archive rather than from any per-filing fetch.
    from connectors.sec_proxy import pay_versus_performance, pay_vs_tsr, proxy_filings
    from connectors.sec_8k import sp500_ciks
    ciks = sorted(sp500_ciks())
    pvp = pay_versus_performance(ciks)
    pay_vs_tsr(pvp)
    return proxy_filings(ciks)


@job("sec_13g", schedule="weekly_thursday",
     desc="Schedule 13G passive 5% holders -- the baseline that makes 13D meaningful")
def _sec_13g():
    from connectors.sec_13g import ingest, latest_quarter
    year, quarter = latest_quarter()
    return ingest(year, quarter)


@job("sec_13g_to_13d", schedule="weekly_thursday",
     desc="Investors who filed 13G then switched to 13D on the same issuer")
def _sec_transitions():
    # Runs after sec_13g in registration order, and needs sec_13d too.
    from connectors.sec_13g import transitions
    return transitions()


@job("sec_companyfacts_restatements", schedule="monthly",
     desc="Refresh the 1.4GB XBRL bulk archive and audit which reported figures were later revised")
def _sec_restatements():
    # Monthly, not weekly: the archive is 1.4 GB and restatements move slowly.
    from connectors.sec_companyfacts import fetch_bulk, universe_restatements
    from connectors.sec_8k import sp500_ciks
    fetch_bulk(refetch=True)
    return universe_restatements(sorted(sp500_ciks()))


@job("sec_8k", schedule="weekly_wednesday",
     desc="8-K material events, item-coded -- 1 request per company, not per filing")
def _sec_8k():
    from connectors.sec_8k import eight_k_filings
    return eight_k_filings()


@job("sec_13d", schedule="weekly_tuesday",
     desc="Schedule 13D activist stakes -- whole market, Item 4 intent classified")
def _sec_13d():
    # Whole market on purpose: activists target small and mid caps, and the
    # entire quarter is only ~2,700 filings.
    from connectors.sec_13d import ingest, latest_quarter
    year, quarter = latest_quarter()
    return ingest(year, quarter)


@job("sec_insider", schedule="weekly_monday",
     desc="Insider Forms 3/4/5 via SEC bulk file -- 1 request replaces ~90,000")
def _sec_insider():
    from connectors.sec_insider import (
        cluster_buys, insider_transactions, latest_available_quarter)
    year, quarter = latest_available_quarter()
    tx = insider_transactions(year, quarter)
    cluster_buys(tx)
    return tx


@job("sec_13f", schedule="weekly_friday",
     desc="13F institutional positioning -- polls the filing index, ingests what is new")
def _sec_13f():
    # Weekly poll, not a quarterly cron: filings scatter across the 45-day
    # window (observed 07-22 .. 09-24 for one quarter), and polling also picks
    # up 13F-HR/A amendments for free.
    from connectors.sec_13f import ingest, latest_reported_quarter
    year, quarter = latest_reported_quarter()
    return ingest(year, quarter)


@job("sec_daily_index", desc="Every SEC filing made market-wide today")
def _sec_idx():
    from connectors.sec_edgar import daily_index
    return daily_index(date.today().strftime("%Y%m%d"))


# ---------------------------------------------------------------- RBI
@job("rbi_forex_reserves", schedule="weekly_friday",
     desc="Weekly forex reserves by component (DBIE gateway)")
def _rbi_forex():
    from connectors.rbi_dbie import RBIClient
    return RBIClient().forex_reserves(weeks=52)


@job("rbi_wss", schedule="weekly_friday",
     desc="WSS extract -- bank credit, deposits, money stock, liquidity ops")
def _rbi_wss():
    from connectors.rbi_publications import wss_extract
    return wss_extract()


@job("rbi_money_market", desc="Daily money market operations -- call/repo volumes and rates")
def _rbi_mmo():
    from connectors.rbi_publications import money_market_operations
    return money_market_operations()


@job("rbi_policy_rates", desc="Policy repo/SDF/MSF corridor snapshot")
def _rbi_rates():
    from connectors.rbi_publications import policy_rates
    return policy_rates()


@job("rbi_key_indicators", desc="Policy corridor snapshot: repo/SDF/CRR/SLR/WACR/CPI, each with its own as-of date")
def _rbi_key_indicators():
    from connectors.rbi_dbie import RBIClient
    return RBIClient().key_indicators()


@job("rbi_press_index", desc="RBI press release index -- LAF auctions, operational actions")
def _rbi_press():
    from connectors.rbi_publications import press_release_index
    return press_release_index()


@job("rbi_banking_ratios", schedule="weekly_friday",
     desc="NSDP fortnightly credit-deposit / cash-deposit ratios")
def _rbi_ratios():
    from connectors.rbi_publications import nsdp_banking_ratios
    return nsdp_banking_ratios()


# ---------------------------------------------------------------- Phase 7: OSINT / geospatial
# Registered BEFORE the cross-asset block below, which must stay last (see its
# docstring). These have no dependency on it and it has none on them.

@job("portwatch_chokepoints",
     desc="Daily vessel transits through 28 maritime chokepoints (IMF PortWatch) -- "
          "the crude-supply and freight leg for India refiners/shippers")
def _portwatch_chokepoints():
    from connectors.chokepoints import portwatch_chokepoints
    # Incremental: a 30-day trailing window re-fetches recently revised days
    # cheaply. The layer publishes ~3-9 days behind, so a 7-day window would
    # sometimes fetch nothing at all.
    since = (date.today() - timedelta(days=30)).isoformat()
    return portwatch_chokepoints(since=since)


@job("noaa_oni", schedule="monthly",
     desc="ENSO / El Nino index -- the leading prior on the India monsoon")
def _noaa_oni():
    from connectors.climate import noaa_oni
    return noaa_oni()


@job("cpc_rainfall",
     desc="Daily gauge-analysed rainfall over India and four sub-regions (NOAA CPC) -- "
          "stands in for IMD, which is pending API approval")
def _cpc_rainfall():
    from connectors.climate import cpc_india_rainfall
    # CPC publishes 1-2 days behind; ask for 3 days back so a normal lag is
    # never mistaken for a failure.
    return cpc_india_rainfall((date.today() - timedelta(days=3)).isoformat())


@job("gdelt_events",
     desc="GDELT daily event stream filtered to the India/Gulf energy universe")
def _gdelt_events():
    from connectors.geopolitics import gdelt_daily_events
    # ~2-day publication lag: yesterday's file 404s.
    return gdelt_daily_events((date.today() - timedelta(days=3)).isoformat())


@job("gdacs_alerts",
     desc="GDACS cyclone/flood/quake alerts -- port and refinery disruption warnings")
def _gdacs_alerts():
    from connectors.geopolitics import gdacs_alerts
    return gdacs_alerts()


@job("opensky_states",
     desc="ADS-B snapshot over Jamnagar/Mumbai/Delhi/Hormuz. SNAPSHOT ONLY -- "
          "there is no history before what we collect, and no ownership mapping")
def _opensky_states():
    from connectors.flights import opensky_states
    out = None
    for box in ("jamnagar", "mumbai", "delhi_ncr", "hormuz"):
        out = opensky_states(box)
    return out


@job("analytics_supply_chain",
     desc="DERIVED: chokepoint z-scores + the reroute mass balance that separates "
          "'traffic rerouted' (freight story) from 'traffic destroyed' (supply story)")
def _analytics_supply_chain():
    from analytics.supply_chain import (
        chokepoint_zscore, current_regime, regime_breaks, reroute_balance,
    )
    chokepoint_zscore()
    reroute_balance()
    # Regime detection needs multi-year history to separate a step from the
    # annual cycle, so on a thin archive it legitimately finds nothing. That
    # is not a pipeline failure -- but it must be visible, not swallowed.
    try:
        regime_breaks()
        return current_regime()
    except RuntimeError as exc:
        print(f"    regime detection skipped: {exc}")
        return None


@job("imd_rainfall", schedule="monthly",
     desc="IMD's OWN 0.25-degree gridded daily rainfall -- the authoritative India "
          "series, via the public NetCDF download form (no API approval needed)")
def _imd_rainfall():
    from connectors.imd import available_years, imd_gridded_rainfall, imd_session
    from core.storage import db, register_views

    # Incremental by year: IMD publishes annual files in arrears, so the only
    # work each month is checking whether a new year appeared. Re-downloading
    # 30 settled years monthly would be 750 MB of pointless traffic.
    con = db(read_only=True)
    try:
        have = set()
        if "imd_gridded_rainfall" in register_views(con):
            have = {int(y) for (y,) in con.execute(
                "SELECT DISTINCT year FROM imd_gridded_rainfall").fetchall()}
    finally:
        con.close()

    f = imd_session()
    wanted = [y for y in available_years(f) if y not in have]
    if not wanted:
        print("    IMD: no newly published year")
        return None
    out = None
    for year in wanted:
        print(f"    IMD: fetching {year}")
        out = imd_gridded_rainfall(year, fetcher=f)
    return out


@job("analytics_monsoon",
     desc="DERIVED: ENSO state, season-to-date rainfall, and departure from the "
          "IMD 1991-2020 normal (CPC observations bias-corrected onto IMD's basis)")
def _analytics_monsoon():
    from analytics.monsoon import enso_state, monsoon_departure, monsoon_progress
    enso_state()
    out = None
    for name, fn in (("monsoon_progress", monsoon_progress),
                     ("monsoon_departure", monsoon_departure)):
        try:
            out = fn()
        except RuntimeError as exc:
            # Outside June-September there is no season to report, and before
            # the IMD/CPC backfills there is no climatology. Neither is a
            # pipeline failure, but both must be visible rather than swallowed.
            print(f"    {name} skipped: {exc}")
    return out


# ---------------------------------------------------------------- Cross-asset analytics
@job("analytics_cross_asset_correlation", schedule="weekdays",
     desc="DERIVED: NIFTYBEES vs commodity (daily) and vs RBI macro (monthly) correlation -- "
          "registered LAST, after analytics_correlation AND every commodity/RBI job above "
          "it reads from (mcx_bhavcopy, cme_settlements, ibja_rates, india_gold_premium, "
          "rbi_forex_reserves, rbi_policy_rates, rbi_key_indicators) -- run_daily executes "
          "in registration order, so this must be the last thing defined in the file")
def _analytics_cross_asset_correlation():
    from analytics.cross_asset_correlation import (
        equity_commodity_correlation, equity_macro_correlation,
    )
    equity_commodity_correlation()
    return equity_macro_correlation()


def _plan(targets, on, run_all):
    """Resolve the target list into (name, will_run, reason) rows."""
    rows = []
    for name in targets:
        spec = JOBS[name]
        due = run_all or is_due(spec["schedule"], on)
        rows.append((name, spec, due))
    return rows


def cmd_dry_run(targets, on, run_all):
    """List what would run. Imports nothing, fetches nothing, writes nothing."""
    rows = _plan(targets, on, run_all)
    will = [r for r in rows if r[2]]

    print(f"DRY RUN -- no fetches, no writes.  as-of {on.isoformat()} ({on.strftime('%A')})\n")

    print("settings:")
    for k, v in describe().items():
        print(f"  {k:24s} {v}")

    print(f"\njobs ({len(will)} of {len(rows)} would run):")
    print(f"  {'':5s}{'JOB':22s}{'SCHEDULE':17s}DESCRIPTION")
    for name, spec, due in rows:
        mark = "RUN " if due else "skip"
        print(f"  {mark:5s}{name:22s}{spec['schedule']:17s}{spec['desc']}")

    skipped = [n for n, _, d in rows if not d]
    if skipped:
        print(f"\nnot due today: {', '.join(skipped)}")
    print(f"\nwould run {len(will)} job(s). Re-run without --dry-run to execute.")
    return 0


def cmd_run(targets, on, run_all):
    rows = _plan(targets, on, run_all)
    results = {}
    for name, spec, due in rows:
        if not due:
            results[name] = "SKIP"
            print(f"  SKIP  {name} (schedule={spec['schedule']})")
            continue
        try:
            spec["fn"]()
            results[name] = "OK"
            print(f"  OK    {name}")
        except Exception as e:  # noqa
            results[name] = f"FAIL: {e}"
            print(f"  FAIL  {name}: {e}")
            traceback.print_exc()

    fails = [k for k, v in results.items() if v.startswith("FAIL")]
    ran = [k for k, v in results.items() if v != "SKIP"]
    print(f"\n{len(ran) - len(fails)}/{len(ran)} succeeded")

    # Silent breakage is the #1 killer of scraped pipelines -- a job that stops
    # running looks identical to a market with no news.
    from core.alerting import alert_failures
    outcome = alert_failures(results)
    if fails:
        print("Failed:", ", ".join(fails))
        if outcome["sent"]:
            print("  alert sent")
        elif not any(outcome["channels"].values()):
            # Say this loudly: the failures went nowhere. Unconfigured alerting
            # looks exactly like healthy alerting until the day it matters.
            print("  NO ALERT CHANNEL CONFIGURED -- set ALERT_WEBHOOK_URL in .env")
        else:
            print("  alert channel configured but the send did not succeed")

    # A job that FAILS shows up above. A job that was never INVOKED this run
    # (a narrow --job command, day after day) shows up nowhere in `results`
    # -- Phase 8 found nine datasets stuck stale for four days with every
    # run reporting clean, because none of those runs happened to include
    # them. This checks the warehouse's actual freshness, independent of
    # what this particular invocation touched.
    from core.alerting import alert_staleness
    from core.quality import quality_report
    STALE_AFTER_HOURS = 48.0
    report = quality_report(stale_after_hours=STALE_AFTER_HOURS)
    stale = report[report["status"] == "STALE"]["view"].tolist() if not report.empty else []
    if stale:
        print(f"\n{len(stale)} dataset(s) STALE (no fresh bytes in "
              f"{STALE_AFTER_HOURS:.0f}h+): {', '.join(stale)}")
        stale_outcome = alert_staleness(stale, STALE_AFTER_HOURS)
        if stale_outcome["sent"]:
            print("  staleness alert sent")
        elif not any(stale_outcome["channels"].values()):
            print("  NO ALERT CHANNEL CONFIGURED -- set ALERT_WEBHOOK_URL in .env "
                  "(also visible any time on the dashboard's top banner)")
        else:
            print("  alert channel configured but the send did not succeed")

    return 1 if (fails or stale) else 0


def main():
    ap = argparse.ArgumentParser(description="quantdata daily orchestration")
    ap.add_argument("--job", action="append",
                    help="run only this job (repeatable)")
    ap.add_argument("--dry-run", action="store_true",
                    help="list jobs that would run, without executing them")
    ap.add_argument("--all", action="store_true",
                    help="ignore schedules; treat every job as due")
    ap.add_argument("--as-of", metavar="YYYY-MM-DD",
                    help="evaluate schedules as of this date (default: today)")
    args = ap.parse_args()

    on = date.fromisoformat(args.as_of) if args.as_of else date.today()

    targets = args.job or list(JOBS)
    unknown = [t for t in targets if t not in JOBS]
    if unknown:
        ap.error(f"unknown job(s): {', '.join(unknown)}. Known: {', '.join(JOBS)}")

    # An explicitly named job is always intended to run.
    run_all = args.all or bool(args.job)

    if args.dry_run:
        raise SystemExit(cmd_dry_run(targets, on, run_all))
    raise SystemExit(cmd_run(targets, on, run_all))


if __name__ == "__main__":
    main()
