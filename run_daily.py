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
@job("nse_bhavcopy", desc="NSE EOD prices + delivery percentage")
def _nse_bhavcopy():
    from connectors.nse_bse import nse_bhavcopy
    return nse_bhavcopy(date.today())


@job("nse_participant_oi", desc="FII/DII/pro/retail open interest -- institutional positioning")
def _participant_oi():
    from connectors.nse_bse import nse_participant_oi
    return nse_participant_oi(date.today())


@job("nse_fii_dii", desc="FII/DII cash market flows")
def _fii_dii():
    from connectors.nse_bse import nse_fii_dii
    return nse_fii_dii()


@job("nse_results", desc="Quarterly results index (XBRL attachments)")
def _results():
    from connectors.nse_bse import nse_financial_results
    return nse_financial_results("Quarterly")


@job("bse_announcements", desc="BSE corporate announcements (~5k companies)")
def _bse_ann():
    from connectors.nse_bse import bse_announcements
    return bse_announcements(date.today() - timedelta(days=1), date.today())


# ---------------------------------------------------------------- Commodities
@job("mcx_bhavcopy", desc="MCX futures settlements -- India commodity leg")
def _mcx():
    from connectors.commodities import mcx_bhavcopy
    return mcx_bhavcopy(date.today())


@job("ibja_rates", desc="IBJA physical gold/silver rates by purity")
def _ibja():
    from connectors.commodities import ibja_rates
    return ibja_rates()


@job("cme_settlements", desc="CME/COMEX settlements -- global benchmark leg")
def _cme():
    from connectors.commodities import cme_settlements, CME_PRODUCTS
    return {p: cme_settlements(p) for p in CME_PRODUCTS}


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
    if fails:
        print("Failed:", ", ".join(fails))
        # Phase 6: alert here. Silent breakage is the #1 killer of scraped
        # pipelines -- a job that stops running looks identical to a market
        # with no news.
    return 1 if fails else 0


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
