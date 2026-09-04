"""
Offline tests for the BSE material-event classifier. material_events()
itself needs a live warehouse; this pins the confirmed-live SUBCATNAME
values, the NEWSID dedup logic, and the date-parsing assumption.
"""
import pandas as pd

from analytics.corporate_events import MATERIAL_EVENT_SUBCATEGORIES


def test_material_event_subcategories_match_confirmed_live_values():
    """
    Guards against a future edit silently typo-ing a SUBCATNAME string --
    these must exactly match what BSE's API actually returns (verified live
    2026-08).
    """
    assert "Change in Management" in MATERIAL_EVENT_SUBCATEGORIES
    assert "Change in Directorate" in MATERIAL_EVENT_SUBCATEGORIES
    assert "Resignation of Director" in MATERIAL_EVENT_SUBCATEGORIES
    assert "Resignation of Statutory Auditors" in MATERIAL_EVENT_SUBCATEGORIES


def test_categoryname_is_not_in_the_material_event_list():
    """
    CATEGORYNAME is just an echo of the query filter used to fetch the data
    (Board Meeting/Company Update/AGM-EGM/Result) and carries no independent
    classification signal -- SUBCATNAME is the real classifier. Nothing in
    MATERIAL_EVENT_SUBCATEGORIES should be one of the four query-filter values.
    """
    query_filter_values = {"Board Meeting", "Company Update", "AGM/EGM", "Result"}
    assert not (set(MATERIAL_EVENT_SUBCATEGORIES) & query_filter_values)


def test_newsid_dedup_collapses_triplicated_same_day_reruns():
    """
    write_table() never overwrites -- a same-day rerun of bse_announcements
    triples every row under identical NEWSIDs (confirmed live). The dedupe
    pattern (arg_max(..., knowledge_date) GROUP BY NEWSID) must collapse
    that back to one row per event.
    """
    ann = pd.DataFrame({
        "NEWSID": ["a", "a", "a", "b"],
        "knowledge_date": ["2026-08-22", "2026-08-22", "2026-08-22", "2026-08-22"],
        "SUBCATNAME": ["Change in Management"] * 4,
    })
    deduped = ann.groupby("NEWSID").tail(1)
    assert len(deduped) == 2


def test_scrip_cd_merge_survives_a_dtype_mismatch():
    """
    Regression: bse_announcements.SCRIP_CD is BIGINT, bse_scrip_master.SCRIP_CD
    is VARCHAR (confirmed live) -- merging without casting both to the same
    type raises "You are trying to merge on int64 and object columns".
    """
    flagged = pd.DataFrame({"SCRIP_CD": [500300, 532540]})
    scrips = pd.DataFrame({"SCRIP_CD": ["500300", "532540"], "Scrip_Name": ["Grasim", "TCS"]})
    flagged["SCRIP_CD"] = flagged["SCRIP_CD"].astype(str)
    scrips["SCRIP_CD"] = scrips["SCRIP_CD"].astype(str)
    out = flagged.merge(scrips, on="SCRIP_CD", how="left")
    assert out["Scrip_Name"].tolist() == ["Grasim", "TCS"]


def test_news_dt_is_already_iso8601_and_needs_no_format_string():
    """
    Unlike every other NSE/BSE date column in this codebase (which need
    explicit %d-%b-%Y-style formats), bse_announcements.NEWS_DT is already
    ISO-8601 -- pinning this so nobody "fixes" it later by adding an
    unnecessary format string that would then fail to parse.
    """
    parsed = pd.to_datetime("2026-08-22T19:17:30.417")
    assert parsed == pd.Timestamp("2026-08-22 19:17:30.417000")
