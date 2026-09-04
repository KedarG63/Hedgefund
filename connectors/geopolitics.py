"""
GEOPOLITICS -- GDELT event stream and GDACS disaster alerts.

WHY THIS MATTERS FOR THIS BOOK
  Two different jobs.

  GDELT is a machine-coded census of world news: every event it detects is
  tagged with actors, a CAMEO event code, a Goldstein cooperation/conflict
  score, a tone, and a geography. Aggregated daily and filtered to the
  countries that set India's energy import bill, it is a quantitative
  conflict/tension series -- the thing that moved under the 2026 Hormuz
  closure, which connectors/chokepoints.py measures the physical consequence
  of. One reads intent, the other reads ships.

  GDACS is narrower and more immediate: cyclone, flood and earthquake alerts
  with severity. A cyclone making landfall on the Indian east coast shuts
  ports and refineries for days -- a dated, checkable operational event.

LEGAL -- READ THIS BEFORE EXTENDING
  GDELT indexes third-party news and THE UNDERLYING ARTICLE TEXT IS
  COPYRIGHTED. We keep the event metadata GDELT itself computes (actors,
  codes, scores, geography) and the source URL, which is a reference, not
  content. Do NOT add article-body fetching or archiving to this module.

ENDPOINT STATE, VERIFIED LIVE 2026-09-02
  GDELT 1.0 daily files at data.gdeltproject.org/events/<YYYYMMDD>.export.CSV.zip
  are the right unit of work: ONE ~6 MB file per day instead of the 96
  fifteen-minute GDELT 2.0 files covering the same period (rule 4 -- prefer a
  bulk file to N requests). Lag is ~2 days: 20260831 was present on
  2026-09-02 while 20260901 still 404'd.

  All GDELT URLs 301-redirect. core.http.Fetcher follows redirects; a bare
  client that does not gets an empty body and reads it as "no data".

THE DATE TRAP -- the same lesson as NSE's broadCastDate
  A daily file is keyed on DATEADDED (when GDELT INGESTED the story), not on
  SQLDATE (when the event happened). They are frequently different: the very
  first row of 20260831.export.CSV is an event dated SQLDATE=20250831, a full
  year earlier, ingested from a retrospective article.

  Measured on the 2026-08-31 file over this module's country universe: 97.8%
  of rows have SQLDATE == DATEADDED, and the remaining 2.2% (~900 rows) trail
  back as far as 2016-09-02. So the trap is small but real and permanent --
  roughly a thousand backdated rows a day, with a ten-year tail.

  So: filter and sort by SQLDATE for "what happened", by DATEADDED for "what
  was reported". Treating the filename as the event date silently mixes a
  decade of retrospective coverage into today's signal. Both columns are kept
  below precisely so a caller cannot avoid choosing.
"""
from __future__ import annotations

import io
import zipfile

import pandas as pd
from lxml import etree

from core.http import Fetcher
from core.storage import save_raw, write_table

GDELT_DAILY = "http://data.gdeltproject.org/events/{day}.export.CSV.zip"
GDACS_RSS = "https://www.gdacs.org/xml/rss.xml"

# GDELT 1.0 export layout: 58 tab-separated columns, NO header row.
# Verified by downloading 20260831.export.CSV and counting -- not taken from
# the codebook on faith.
GDELT_COLUMNS = [
    "GlobalEventID", "SQLDATE", "MonthYear", "Year", "FractionDate",
    "Actor1Code", "Actor1Name", "Actor1CountryCode", "Actor1KnownGroupCode",
    "Actor1EthnicCode", "Actor1Religion1Code", "Actor1Religion2Code",
    "Actor1Type1Code", "Actor1Type2Code", "Actor1Type3Code",
    "Actor2Code", "Actor2Name", "Actor2CountryCode", "Actor2KnownGroupCode",
    "Actor2EthnicCode", "Actor2Religion1Code", "Actor2Religion2Code",
    "Actor2Type1Code", "Actor2Type2Code", "Actor2Type3Code",
    "IsRootEvent", "EventCode", "EventBaseCode", "EventRootCode", "QuadClass",
    "GoldsteinScale", "NumMentions", "NumSources", "NumArticles", "AvgTone",
    "Actor1Geo_Type", "Actor1Geo_FullName", "Actor1Geo_CountryCode",
    "Actor1Geo_ADM1Code", "Actor1Geo_Lat", "Actor1Geo_Long", "Actor1Geo_FeatureID",
    "Actor2Geo_Type", "Actor2Geo_FullName", "Actor2Geo_CountryCode",
    "Actor2Geo_ADM1Code", "Actor2Geo_Lat", "Actor2Geo_Long", "Actor2Geo_FeatureID",
    "ActionGeo_Type", "ActionGeo_FullName", "ActionGeo_CountryCode",
    "ActionGeo_ADM1Code", "ActionGeo_Lat", "ActionGeo_Long", "ActionGeo_FeatureID",
    "DATEADDED", "SOURCEURL",
]

# The universe. Filtering happens BEFORE anything is kept (rule 5): a daily
# file is ~300k rows and we care about a few thousand.
#   India itself, the Gulf suppliers, the transit states, and the powers whose
#   actions move all of the above.
#
# TWO CODE SYSTEMS IN ONE FILE -- the trap that makes this a dict, not a set.
# GDELT tags ACTORS with CAMEO 3-letter codes (IRN, USA) but tags ACTION
# GEOGRAPHY with FIPS 10-4 2-letter codes (IR, US). Filtering all three fields
# against one 3-letter set silently drops the geography condition entirely --
# it can never match -- so the filter quietly does less than it claims while
# still returning plenty of rows from the actor fields. Caught by a unit test,
# not by live output, because live output looked perfectly reasonable.
#
# And FIPS is NOT ISO: Iraq is IZ, Kuwait KU, Oman MU, Yemen YM, Russia RS,
# China CH. Verified against a real export's ActionGeo_CountryCode counts.
ENERGY_COUNTRIES_CAMEO = frozenset({
    "IND",                                             # the book's home market
    "IRN", "SAU", "ARE", "IRQ", "QAT", "KWT", "OMN",   # Gulf supply
    "YEM", "EGY",                                      # Bab el-Mandeb / Suez transit
    "ISR", "USA", "RUS", "CHN",                        # the actors that move them
})

ENERGY_COUNTRIES_FIPS = frozenset({
    "IN",
    "IR", "SA", "AE", "IZ", "QA", "KU", "MU",
    "YM", "EG",
    "IS", "US", "RS", "CH",
})

# Backwards-compatible alias for callers that just want "the universe".
ENERGY_COUNTRIES = ENERGY_COUNTRIES_CAMEO

# CAMEO root codes. 14-20 are the coercive end (protest through unconventional
# mass violence); 17-20 are where supply disruption actually lives.
CONFLICT_ROOT_CODES = frozenset({"14", "15", "16", "17", "18", "19", "20"})

QUAD_CLASS = {1: "verbal_cooperation", 2: "material_cooperation",
              3: "verbal_conflict", 4: "material_conflict"}


def gdelt_daily_events(day: str, countries: frozenset = ENERGY_COUNTRIES_CAMEO,
                       geo_countries: frozenset | None = None,
                       conflict_only: bool = False, persist: bool = True) -> pd.DataFrame:
    """
    One day of GDELT events, filtered to a country universe.

    `day`             ISO date ("2026-08-31"). ~2-day publication lag.
    `countries`       CAMEO 3-letter codes matched against the ACTOR fields.
                      None/empty keeps everything (~300k rows -- rarely what
                      you want).
    `geo_countries`   FIPS 10-4 2-letter codes matched against ACTION
                      GEOGRAPHY. Defaults to the FIPS twin of the default
                      universe. Passing a 3-letter set here matches nothing --
                      see the ENERGY_COUNTRIES_* comment above.
    `conflict_only`   restrict to CAMEO root codes 14-20.

    WHY IT MATTERS: a daily conflict/tone series over the countries that set
    India's crude import bill. Pairs with connectors/chokepoints.py -- this is
    the intent, that is the physical consequence.

    A row is kept if EITHER actor's country OR the action geography is in the
    universe: an Iranian action in Yemeni waters matters whichever field
    carries the code.

    Returns the filtered event rows. See the module docstring on SQLDATE vs
    DATEADDED before using either as "the" date.
    """
    stamp = pd.to_datetime(day).strftime("%Y%m%d")
    url = GDELT_DAILY.format(day=stamp)

    r = Fetcher().get(url)
    save_raw("gdelt", "daily_events", r.content, "zip", {"url": url, "day": stamp})

    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        members = z.namelist()
        if not members:
            raise RuntimeError(f"GDELT {stamp}: archive is empty")
        with z.open(members[0]) as fh:
            df = pd.read_csv(fh, sep="\t", header=None, names=GDELT_COLUMNS,
                             dtype=str, quoting=3, on_bad_lines="skip")

    if df.empty:
        raise RuntimeError(f"GDELT {stamp}: file parsed to zero rows")
    if len(df.columns) != len(GDELT_COLUMNS):
        raise RuntimeError(
            f"GDELT {stamp}: expected {len(GDELT_COLUMNS)} columns, got "
            f"{len(df.columns)} -- the export layout changed."
        )

    if countries:
        # Actors are CAMEO 3-letter; action geography is FIPS 2-letter. Using
        # one set for both makes the geography term dead code -- see the
        # ENERGY_COUNTRIES_* comment above.
        geo_codes = geo_countries if geo_countries is not None else (
            ENERGY_COUNTRIES_FIPS if countries is ENERGY_COUNTRIES_CAMEO else countries
        )
        in_universe = (
            df["Actor1CountryCode"].isin(countries)
            | df["Actor2CountryCode"].isin(countries)
            | df["ActionGeo_CountryCode"].isin(geo_codes)
        )
        df = df[in_universe]
    if conflict_only:
        df = df[df["EventRootCode"].isin(CONFLICT_ROOT_CODES)]

    if df.empty:
        raise RuntimeError(
            f"GDELT {stamp}: no rows survived the universe filter. That is "
            f"never normal for this universe -- check the country codes."
        )

    for col in ("GoldsteinScale", "AvgTone"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("NumMentions", "NumSources", "NumArticles", "QuadClass"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

    # Both dates, explicitly named, so downstream has to pick deliberately.
    df["event_date"] = pd.to_datetime(df["SQLDATE"], format="%Y%m%d",
                                      errors="coerce").dt.date.astype(str)
    df["ingested_date"] = pd.to_datetime(df["DATEADDED"], format="%Y%m%d",
                                         errors="coerce").dt.date.astype(str)
    df["quad_class_label"] = df["QuadClass"].map(QUAD_CLASS)

    df = df.reset_index(drop=True)
    if persist:
        write_table(df, "gdelt", "daily_events")
    return df


# -------------------------------------------------------------------- GDACS
GDACS_NS = {
    "gdacs": "http://www.gdacs.org",
    "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#",
}


def gdacs_alerts(persist: bool = True) -> pd.DataFrame:
    """
    Current global disaster alerts from GDACS (UN/EC Joint Research Centre).

    Cyclones, floods, earthquakes, droughts and wildfires with an alert level
    (Green/Orange/Red), location and population exposure.

    WHY IT MATTERS: a Red or Orange cyclone alert on the Indian east coast is
    an operational warning for ports, refineries and coastal power -- a dated
    event that shows up days later in throughput and insurance. It is the
    fast, coarse complement to the chokepoint series.

    Returns one row per current alert. This is a SNAPSHOT feed, not a history:
    alerts drop off as they expire, which is exactly why every pull is
    archived and stamped with knowledge_date.
    """
    r = Fetcher().get(GDACS_RSS)
    save_raw("gdacs", "alerts", r.content, "xml", {"url": GDACS_RSS})

    root = etree.fromstring(r.content)
    items = root.findall(".//item")
    if not items:
        raise RuntimeError("GDACS: feed parsed but contains no <item> elements")

    def text(node, path, ns=None):
        found = node.find(path, ns) if ns else node.find(path)
        return found.text.strip() if found is not None and found.text else None

    rows = []
    for it in items:
        rows.append({
            "title": text(it, "title"),
            "event_type": text(it, "gdacs:eventtype", GDACS_NS),
            "alert_level": text(it, "gdacs:alertlevel", GDACS_NS),
            "severity": text(it, "gdacs:severity", GDACS_NS),
            "country": text(it, "gdacs:country", GDACS_NS),
            "from_date": text(it, "gdacs:fromdate", GDACS_NS),
            "to_date": text(it, "gdacs:todate", GDACS_NS),
            "population": text(it, "gdacs:population", GDACS_NS),
            "lat": text(it, "geo:lat", GDACS_NS),
            "lon": text(it, "geo:long", GDACS_NS),
            "pub_date": text(it, "pubDate"),
            "link": text(it, "link"),
        })

    out = pd.DataFrame(rows)
    for col in ("lat", "lon"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if out["event_type"].isna().all():
        raise RuntimeError(
            "GDACS: no gdacs:eventtype on any item -- the feed's namespace or "
            "element names changed; update the parser."
        )

    if persist:
        write_table(out, "gdacs", "alerts")
    return out
