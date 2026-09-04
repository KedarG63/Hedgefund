"""
Deterministic, code-filterable material-event flags from BSE's structured
SUBCATNAME classifier -- no LLM involved, this is a controlled-vocabulary
filter, same "DeepSeek stays minimal" spirit as digest.py's scoping.

BSE, not NSE: nse_announcements only carries the coarser `desc` taxonomy
(e.g. "Outcome of Board Meeting", "General Updates") -- confirmed live, no
resignation/management-change bucket in it -- while
bse_announcements.SUBCATNAME is fine-grained enough to filter on directly.

Known limitation carried forward from connectors/nse_bse.py's own docstring:
bse_announcements() caps at 50 rows/category/page and only fetches page 1 --
a category with >50 announcements on one day silently drops the excess. Not
fixed here; noted so a consumer of this module doesn't assume completeness.

Second known limitation, found while building this module: write_table()
never overwrites, so same-day reruns of bse_announcements duplicate rows
under identical NEWSIDs (confirmed live: three 2026-08-22 runs tripled
every row). Every read here goes through core.asof, which collapses each
view on its declared natural key -- the same fix analytics/digest.py and
analytics/outliers.py already needed for their own same-day-vintage problem.
That applies to the bse_scrip_master join as much as to the announcements
themselves: joining to an un-collapsed scrip master multiplied every emitted
event by the number of scrip-master vintages on disk.
"""
from __future__ import annotations

import pandas as pd

from core.asof import asof_sql
from core.storage import db, register_views, write_table

# Material-event SUBCATNAME values, confirmed present in live data. Same
# "documented, provisional, needs periodic review" character as
# BSE_ANNOUNCEMENT_CATEGORIES in connectors/nse_bse.py -- SEBI LODR defines
# more subcategories than this; each addition should be checked against
# real data before being added, not guessed from the regulation text.
MATERIAL_EVENT_SUBCATEGORIES = (
    "Change in Management",
    "Change in Directorate",
    "Resignation of Director",
    "Resignation of Statutory Auditors",
    "Appointment of Statutory Auditor/s",
    "Credit Rating",
    "Scheme of Arrangement",
)

DEFAULT_WINDOW_DAYS = 7


def _query(sql: str) -> pd.DataFrame:
    con = db(read_only=True)
    try:
        register_views(con)
        return con.execute(sql).df()
    finally:
        con.close()


def material_events(window_days: int = DEFAULT_WINDOW_DAYS, persist: bool = True) -> pd.DataFrame:
    """
    Discrete material-event flags per instrument over the trailing window:
    one row per NEWSID filtered to MATERIAL_EVENT_SUBCATEGORIES, joined to
    ISIN via bse_scrip_master. NEWSID-deduped first (see module docstring)
    since a same-day rerun otherwise triples every row.
    """
    # NEWSID is bse_announcements' declared natural key, so the point-in-time
    # read already yields one row per announcement -- no outer collapse needed.
    # This replaces five hand-written arg_max() columns; that form had to name
    # every column, so a column added to the table later was silently dropped,
    # and each arg_max resolved independently (a headline could pair with
    # another vintage's subcategory).
    ann = _query(asof_sql(
        "bse_announcements",
        columns="NEWSID, SCRIP_CD, SLONGNAME AS company_name, "
                "SUBCATNAME AS subcatname, HEADLINE AS headline, NEWS_DT AS news_dt",
    ))
    if ann.empty:
        raise RuntimeError("material_events: bse_announcements has no rows yet.")

    ann["news_dt"] = pd.to_datetime(ann["news_dt"], errors="coerce")  # already ISO-8601
    ann = ann.dropna(subset=["news_dt"])
    cutoff = ann["news_dt"].max() - pd.Timedelta(days=window_days)
    flagged = ann[(ann["news_dt"] >= cutoff)
                  & (ann["subcatname"].isin(MATERIAL_EVENT_SUBCATEGORIES))].copy()

    # POINT-IN-TIME, and not merely for tidiness: bse_scrip_master accumulates
    # a vintage per weekly refresh, and a plain SELECT returns all of them. The
    # merge below is many-to-one only if this side is unique per SCRIP_CD --
    # with four vintages on disk it was many-to-FOUR, so every flagged event
    # was emitted ~4x into derived_corporate_events (confirmed live: 179 rows
    # holding 47 distinct NEWSIDs, the duplicates byte-identical). That
    # inflated every event count downstream.
    scrips = _query(asof_sql(
        "bse_scrip_master", columns="SCRIP_CD, ISIN_NUMBER, Scrip_Name"))
    # bse_announcements.SCRIP_CD is BIGINT, bse_scrip_master.SCRIP_CD is
    # VARCHAR (confirmed live) -- pandas refuses to merge across dtypes, so
    # both sides are cast to string first.
    flagged["SCRIP_CD"] = flagged["SCRIP_CD"].astype(str)
    scrips["SCRIP_CD"] = scrips["SCRIP_CD"].astype(str)
    out = flagged.merge(scrips, on="SCRIP_CD", how="left")
    out["as_of_date"] = ann["news_dt"].max().date().isoformat()
    out = out.sort_values("news_dt", ascending=False, ignore_index=True)

    if persist:
        write_table(out, "derived", "corporate_events")
    return out
