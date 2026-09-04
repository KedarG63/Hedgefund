"""
MARITIME CHOKEPOINTS -- IMF PortWatch daily transit counts.

WHAT THIS IS
  Daily vessel transits through 28 global maritime chokepoints, split by
  vessel class (tanker / dry bulk / container / roro / general cargo), with
  deadweight capacity alongside the counts. Derived from AIS by the IMF and
  UN Global Platform and published as an ArcGIS FeatureServer layer.

WHY IT MATTERS FOR THIS BOOK
  India imports roughly 85% of its crude, much of it Gulf-origin through the
  Strait of Hormuz. Transit counts lead freight rates, refining margins and
  landed crude cost -- RELIANCE (Jamnagar), IOC/BPCL/HPCL, and the shippers
  (SCI, GE Shipping). It is the same structure as the India gold premium in
  commodities.py: a series nobody sells, computable because we own each leg.

  As of this writing Hormuz has been effectively closed since 2026-02-28.
  This is a live position-relevant series, not a hypothetical.

THE SERIES IS VALIDATED -- AND HOW MATTERS
  Hormuz reads 1-9 vessels/day through 2026. That is ~an order of magnitude
  below its own 2019-2026 norm, and an earlier review called the series
  broken on exactly that basis. It is not broken. The full history runs
  55-105 vessels/day for seven straight years, then breaks to 3.2/day in
  2026-03 -- the month of the 2026-02-28 escalation -- and tracks the
  reported chronology month by month afterwards (June MOU reopening = 12.9,
  July breakdown = back to 4.9).

  The proof is a MASS BALANCE, not a plausible-looking level. Across the 2024
  Red Sea crisis, Bab el-Mandeb loses 45.9 vessels/day (77.3 -> 31.4) while
  the Cape of Good Hope gains 46.2 (49.3 -> 95.5) over the same months. Ships
  did not vanish; they sailed around Africa, and a corrupted feed cannot fake
  a conserved balance across a substitute route.

  So: NEVER validate one of these series against a remembered "plausible"
  level. Pull the history, date the structural break, then cross-check the
  substitute route. See PHASE7.md for the full write-up.

ENDPOINT STATE, VERIFIED LIVE 2026-09-01/02
  The layer is a public ArcGIS FeatureServer. Two operational facts shape
  every call:

  SHARED QUOTA  The tenant is throttled at 6,000 request units/minute SHARED
      ACROSS EVERY CONSUMER of that ArcGIS organisation, not per caller. It
      429s unpredictably no matter how polite we are -- four consecutive
      retries at 30s spacing all failed during testing. core.http.Fetcher
      already treats 429 as retryable, which is why this module uses it
      rather than a bare client. Pull once daily and archive. NEVER query
      this live from the dashboard: random empty panels look exactly like a
      broken connector.

  ~9 DAY LAG   Latest available date was 2026-08-23 against a 2026-09-01 run.
      Structural-trend input, not a trading signal. Any UI must show the
      as-of date.

  DATE TYPING  `date` is esriFieldTypeDateOnly and its JSON representation
      DEPENDS ON THE QUERY: "2026-08-23" when named explicitly in outFields,
      but epoch-milliseconds when outFields=*. Both are handled below; a
      parser that assumed one would break the moment the caller changed the
      field list.

NOT VERIFIED -- do not use without checking first
  The PortWatch *ports* layer (PortWatch_ports_database) returned 429 on
  every attempt and has never once succeeded from this machine. A guessed
  ArcGIS Hub bulk-CSV item id returned "Item does not exist or is
  inaccessible". Neither is wired up here; recovering the real item ids needs
  a JS-bundle read, same as tools/discover_rbi_services.py.
"""
from __future__ import annotations

import json

import pandas as pd

from core.http import Fetcher
from core.storage import save_raw, write_table

PORTWATCH_HOST = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
CHOKEPOINTS_URL = f"{PORTWATCH_HOST}/Daily_Chokepoints_Data/FeatureServer/0/query"

# ArcGIS caps a single page well below this; the layer advertises 2000. We ask
# for that and follow exceededTransferLimit rather than trusting one response.
PAGE_SIZE = 2000

# Columns the parser depends on. Extra columns are fine and get carried through.
EXPECTED_COLUMNS = {
    "date", "portid", "portname",
    "n_tanker", "n_container", "n_dry_bulk", "n_general_cargo", "n_roro",
    "n_cargo", "n_total", "capacity",
}

# The chokepoints that matter for an India crude/freight book, in rough order
# of relevance. The connector fetches all 28 -- this is for callers that want
# the short list without hardcoding strings at the call site.
INDIA_RELEVANT = (
    "Strait of Hormuz",       # Gulf crude -- the dominant one for India
    "Bab el-Mandeb Strait",   # Red Sea / Suez approach
    "Suez Canal",             # Europe-Asia
    "Cape of Good Hope",      # the substitute route; rises when the above fall
    "Malacca Strait",         # East-of-India flows
)


def _check_schema(df: pd.DataFrame, expected: set, source: str, dataset: str) -> None:
    """
    Raise if a column this parser depends on has silently disappeared.

    Same contract as nse_bse._check_schema: new columns are harmless, a
    MISSING one means every downstream user of it is now silently wrong.
    """
    missing = expected - set(df.columns)
    if missing:
        raise RuntimeError(
            f"{source}.{dataset}: schema drift -- expected column(s) "
            f"{sorted(missing)} not found. Got: {sorted(df.columns)}. "
            f"The layer's schema changed; update the parser before trusting this data."
        )


def _coerce_date(series: pd.Series) -> pd.Series:
    """
    Normalise PortWatch's two date representations to ISO date strings.

    esriFieldTypeDateOnly serialises as "YYYY-MM-DD" when the field is named
    explicitly in outFields, but as epoch MILLISECONDS when outFields=*. This
    is not documented anywhere -- it was found by running both queries. Guess
    wrong and you get 1970 dates, or a crash on str/int division.
    """
    if series.empty:
        return series.astype(str)
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().all():
        return pd.to_datetime(numeric, unit="ms", errors="coerce").dt.date.astype(str)
    return pd.to_datetime(series, errors="coerce").dt.date.astype(str)


def _query(fetcher: Fetcher, params: dict) -> dict:
    """One ArcGIS query. Raises on the error envelope ArcGIS returns inside a 200."""
    r = fetcher.get(CHOKEPOINTS_URL, params=params)
    payload = r.json()
    # ArcGIS reports failure INSIDE a 200 body. A caller that only checks the
    # HTTP status treats a quota rejection as an empty result set -- which
    # would archive a valid-looking file containing no data.
    if "error" in payload:
        err = payload["error"]
        raise RuntimeError(
            f"PortWatch query failed: code={err.get('code')} "
            f"{err.get('message')} {'; '.join(err.get('details') or [])}"
        )
    return payload


def portwatch_chokepoints(since: str | None = None, until: str | None = None,
                          portnames: tuple[str, ...] | None = None,
                          persist: bool = True) -> pd.DataFrame:
    """
    Daily transit counts and deadweight capacity for PortWatch's 28 maritime
    chokepoints.

    `since`     ISO date ("2026-01-01"). None fetches the full history from
                2019 -- roughly 78,000 rows, which is a handful of pages and
                perfectly reasonable as a one-off backfill.
    `until`     ISO date, inclusive upper bound. Lets a deepening backfill
                fetch ONLY the missing older window instead of re-pulling
                everything already held -- which matters here because the
                ArcGIS tenant is a shared, throttled quota (see above), so
                every page re-fetched is a page someone else cannot have.
    `portnames` restrict to specific chokepoints. None fetches all 28.

    WHY IT MATTERS: see the module docstring. This is the crude-supply and
    freight-rate leg for the India refiners and shippers, and the only series
    here that directly prices a live geopolitical disruption.

    Returns one row per (date, chokepoint).
    """
    # Retry budget is sized against the QUOTA WINDOW, not plucked from the air.
    # The tenant's limit resets per MINUTE, and Fetcher's default 4 retries back
    # off 1+2+4+8s ~= 15s -- structurally incapable of outlasting a 60s window,
    # which is why the first full backfill attempt died partway through with
    # "GET failed after 4 tries". Eight retries reach 1+2+...+128s, so a page
    # can sit out more than two full quota windows before giving up.
    fetcher = Fetcher(min_delay=1.5, max_retries=8)

    clauses = []
    if since:
        # DATE literal, not a string compare -- the field is a date type and a
        # string comparison silently matches nothing.
        clauses.append(f"date >= DATE '{since}'")
    if until:
        clauses.append(f"date <= DATE '{until}'")
    where = " AND ".join(clauses) if clauses else "1=1"
    if portnames:
        quoted = ",".join("'" + p.replace("'", "''") + "'" for p in portnames)
        where += f" AND portname IN ({quoted})"

    rows: list[dict] = []
    offset = 0
    pages = 0
    while True:
        params = {
            "where": where,
            "outFields": "*",
            "orderByFields": "date ASC,portname ASC",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
            "f": "json",
        }
        payload = _query(fetcher, params)
        pages += 1

        # Archive every page verbatim before parsing. Rule 1: a page is the
        # unit of fetch here, so a page is the unit of archive.
        save_raw(
            "portwatch", "chokepoints",
            json.dumps(payload).encode("utf-8"), "json",
            {"url": CHOKEPOINTS_URL, "where": where, "offset": offset,
             "page": pages, "features": len(payload.get("features") or [])},
        )

        features = payload.get("features") or []
        rows.extend(f["attributes"] for f in features)

        if not payload.get("exceededTransferLimit") or not features:
            break
        offset += len(features)

    if not rows:
        raise RuntimeError(
            f"PortWatch returned no rows for where={where!r}. That is never "
            f"normal for this layer -- check the date bound and the layer URL."
        )

    df = pd.DataFrame(rows)
    _check_schema(df, EXPECTED_COLUMNS, "portwatch", "chokepoints")

    df["date"] = _coerce_date(df["date"])
    df = df.dropna(subset=["date"])
    count_cols = [c for c in df.columns if c.startswith(("n_", "capacity"))]
    for col in count_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.drop(columns=[c for c in ("ObjectId",) if c in df.columns])
    df = df.sort_values(["date", "portname"]).reset_index(drop=True)

    if persist:
        write_table(df, "portwatch", "chokepoints")
    return df
