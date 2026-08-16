"""
Daily orchestration. Start with this; graduate to Dagster/Prefect when you have
30+ jobs and need dependency graphs and backfills.

    python run_daily.py            # run everything due now
    python run_daily.py --job nse_bhavcopy
"""
import argparse
import traceback
from datetime import date, timedelta

JOBS = {}


def job(name, schedule="daily"):
    def deco(fn):
        JOBS[name] = {"fn": fn, "schedule": schedule}
        return fn
    return deco


# ---------------------------------------------------------------- India EOD
@job("nse_bhavcopy")
def _nse_bhavcopy():
    from connectors.nse_bse import nse_bhavcopy
    return nse_bhavcopy(date.today())


@job("nse_participant_oi")
def _participant_oi():
    from connectors.nse_bse import nse_participant_oi
    return nse_participant_oi(date.today())


@job("nse_fii_dii")
def _fii_dii():
    from connectors.nse_bse import nse_fii_dii
    return nse_fii_dii()


@job("nse_results")
def _results():
    from connectors.nse_bse import nse_financial_results
    return nse_financial_results("Quarterly")


@job("bse_announcements")
def _bse_ann():
    from connectors.nse_bse import bse_announcements
    return bse_announcements(date.today() - timedelta(days=1), date.today())


# ---------------------------------------------------------------- Commodities
@job("mcx_bhavcopy")
def _mcx():
    from connectors.commodities import mcx_bhavcopy
    return mcx_bhavcopy(date.today())


@job("ibja_rates")
def _ibja():
    from connectors.commodities import ibja_rates
    return ibja_rates()


@job("cme_settlements")
def _cme():
    from connectors.commodities import cme_settlements, CME_PRODUCTS
    return {p: cme_settlements(p) for p in CME_PRODUCTS}


# ---------------------------------------------------------------- US
@job("sec_daily_index")
def _sec_idx():
    from connectors.sec_edgar import daily_index
    return daily_index(date.today().strftime("%Y%m%d"))


# ---------------------------------------------------------------- RBI
@job("rbi_wss", schedule="weekly_friday")
def _rbi_wss():
    from connectors.rbi_dbie import fetch_publication
    return fetch_publication("wss")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job")
    args = ap.parse_args()

    targets = [args.job] if args.job else list(JOBS)
    results = {}
    for name in targets:
        try:
            JOBS[name]["fn"]()
            results[name] = "OK"
            print(f"  OK    {name}")
        except Exception as e:
            results[name] = f"FAIL: {e}"
            print(f"  FAIL  {name}: {e}")
            traceback.print_exc()

    fails = [k for k, v in results.items() if v != "OK"]
    print(f"\n{len(results) - len(fails)}/{len(results)} succeeded")
    if fails:
        print("Failed:", ", ".join(fails))
        # In production: alert here. Silent breakage is the #1 killer of
        # scraped pipelines -- a job that stops running looks identical to a
        # market with no news.


if __name__ == "__main__":
    main()
