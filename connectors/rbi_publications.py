"""
RBI fixed-URL publications -- Path B.

WHY THIS MODULE EXISTS ALONGSIDE rbi_dbie.py
--------------------------------------------
DBIE (data.rbi.org.in) has exactly one per-series endpoint we can drive today:
forex reserves. Everything else on that SPA routes through a generic Impala
query engine addressed by element codes that are ENCRYPTED client-side, so it
cannot be driven without either breaking that scheme or a captured cURL.

The publications below need no reverse engineering at all. They are officially
published on a known schedule at stable URLs, and they carry the series DBIE
hides behind its query engine:

  Weekly Statistical Supplement (WSS)  bank credit, deposits, money stock,
                                       liquidity operations, forex reserves
  Money Market Operations              daily call/repo volumes and rates
  NSDP policy rates                    policy repo / SDF / MSF / bank rate history

BUILD_GUIDE calls Path B "slower but bulletproof -- the backstop that keeps
running when the SPA changes its API". That is exactly its role here.

WHAT WAS VERIFIED LIVE (2026-08-18)
-----------------------------------
- rbi.org.in serves `content-encoding: br`. Without a Brotli decoder installed
  every response decodes to binary garbage while still returning HTTP 200 --
  see the brotli pin in requirements.txt. This is why `Scripts/WSSView.aspx`
  first appeared to be an empty page.
- `Scripts/WSSView.aspx` is a navigation page, NOT the data. The real WSS page
  (`BS_viewWss.aspx`) is an ASP.NET __VIEWSTATE calendar with no data links.
- The WSS content IS reachable without touching __VIEWSTATE: RBI also publishes
  it as a press release ("Weekly Statistical Supplement - Extract"), which is
  plain HTML tables. That is the route used here.

TABLE SHAPE, AND WHY OUTPUT IS LONG-FORMAT
------------------------------------------
These tables carry a unit row, two-to-three levels of merged header, a row of
column numbers, data rows, then merged footnote rows. Column layouts differ per
table and shift between releases (a "Variation over" block gains a year every
April). Pinning a wide schema would break on those shifts, so every table is
melted to tidy rows of (table_no, table_title, row_label, series, value). New or
renamed columns then appear as new rows rather than as a schema break.
"""
from __future__ import annotations

import io
import re
from datetime import datetime

import pandas as pd

from core.http import Fetcher
from core.storage import save_raw, write_table

BASE = "https://rbi.org.in"
PRESS_INDEX = f"{BASE}/Scripts/BS_PressReleaseDisplay.aspx"
PRESS_ITEM = f"{BASE}/Scripts/BS_PressReleaseDisplay.aspx?prid={{prid}}"
NSDP_RATES = "https://www.rbi.org.in/scripts/BS_NSDPDisplay.aspx?param=4"

# Landing/navigation pages. Kept because they are stable entry points, but note
# WSSView.aspx is a nav page -- the data comes via the press release.
PUBLICATIONS = {
    "wss": f"{BASE}/Scripts/WSSView.aspx",
    "wss_calendar": f"{BASE}/Scripts/BS_viewWss.aspx",
    "bulletin": f"{BASE}/Scripts/BS_ViewBulletin.aspx",
    "handbook": f"{BASE}/Scripts/AnnualPublications.aspx?head=Handbook%20of%20Statistics%20on%20Indian%20Economy",
    "press_releases": PRESS_INDEX,
}

# <a class='link2' href=BS_PressReleaseDisplay.aspx?prid=63390>TITLE</a>
PR_LINK_RE = re.compile(
    r"<a\s+class='link2'\s+href=BS_PressReleaseDisplay\.aspx\?prid=(\d+)>(.*?)</a>",
    re.I | re.S,
)
PR_DATE_RE = re.compile(
    r"class=\"tableheader\"[^>]*>\s*<b>\s*([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})", re.I
)


def _session() -> Fetcher:
    return Fetcher(base_headers={"Referer": BASE + "/"}, min_delay=0.7)


def _text(x) -> str:
    """Collapse the whitespace padding RBI's HTML is full of."""
    s = re.sub(r"\s+", " ", str(x)).strip()
    return "" if s.lower() in ("nan", "none") else s


NUMERIC_CELL_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?$")


def _is_index_row(vals: list[str]) -> bool:
    """
    The '1 2 3 4 ...' row that terminates the header block.

    Must not match a row of YEARS. RBI's NSDP header carries
    '2025 2025 2026 2026 ...', which an all-digits test treats as the index row
    and so discards every real period label below it. Column indices are small
    and start at 1; years are not.
    """
    nums = [v for v in vals[1:] if v]
    if len(nums) < 2 or not all(v.isdigit() for v in nums):
        return False
    ints = [int(v) for v in nums]
    return min(ints) == 1 and max(ints) <= 60


def _is_unit_row(vals: list[str]) -> bool:
    """'(₹ Crore)' / '(Per cent)' banner repeated across the width."""
    nz = [v for v in vals if v]
    return bool(nz) and all(v.startswith("(") for v in nz)


def _is_merged_row(vals: list[str]) -> bool:
    """A single value spanning every column -- footnote or section banner."""
    nz = [v for v in vals if v]
    return len(nz) > 1 and len(set(nz)) == 1


def _first_data_row(df: pd.DataFrame) -> int | None:
    """
    First row that looks like data: a label plus at least one numeric cell.
    Used when a table has no column-index row (RBI's Money Market Operations
    tables do not), so the header block can still be bounded correctly.
    """
    for i in range(len(df)):
        vals = list(df.iloc[i])
        if not vals[0] or _is_unit_row(vals) or _is_merged_row(vals):
            continue
        if any(v and NUMERIC_CELL_RE.match(v) for v in vals[1:]):
            return i
    return None


def tidy_table(raw: pd.DataFrame, table_no: int) -> pd.DataFrame:
    """
    Melt one RBI HTML table into tidy rows.

    Returns columns: table_no, table_title, row_label, series, value.
    Footnote rows (the same text merged across every column) are dropped, but
    the table title is preserved so nothing is silently lost.
    """
    title = re.sub(r"\.\d+$", "", _text(raw.columns[0]))
    # read_html names columns "0","1",... when a table has no header row; that is
    # a positional artefact, not a title. Blank it so it never reads as data.
    if title.isdigit():
        title = ""

    df = raw.copy()
    df.columns = range(df.shape[1])
    df = df.map(_text)

    # Bound the multi-level header block. Prefer the explicit column-index row;
    # fall back to "everything above the first data row" when there is none.
    header_end = None
    for i in range(min(len(df), 10)):
        if _is_index_row(list(df.iloc[i])):
            header_end = i
            break

    if header_end is not None:
        header_rows = list(range(header_end))
        data = df.iloc[header_end + 1:]
    else:
        first = _first_data_row(df)
        if first is None:
            return pd.DataFrame()
        header_rows = list(range(first))
        data = df.iloc[first:]

    # Build a column name per position by joining its distinct header levels.
    # Keep years (2025, 2026) -- they are real period labels; drop unit banners
    # and leftover small column indices.
    names = {}
    for c in df.columns:
        parts, seen = [], set()
        for r in header_rows:
            v = df.iat[r, c]
            if not v or v in seen or v.startswith("("):
                continue
            if v.isdigit() and int(v) <= 60:
                continue
            row_vals = list(df.iloc[r])
            if _is_merged_row(row_vals):
                continue
            seen.add(v)
            parts.append(v)
        names[c] = " / ".join(parts) or f"col{c}"

    out = []
    for _, row in data.iterrows():
        vals = list(row)
        label = vals[0]
        if not label or _is_unit_row(vals) or _is_merged_row(vals):
            continue
        for c in range(1, len(vals)):
            v = vals[c]
            if v in ("", "-", "–"):
                continue
            out.append({
                "table_no": table_no,
                "table_title": title,
                "row_label": label,
                "series": names[c],
                "value": v,
            })

    return pd.DataFrame(out)


def _numeric(s: pd.Series) -> pd.Series:
    """RBI uses commas, and parenthesised negatives in places."""
    cleaned = (s.astype(str)
                .str.replace(",", "", regex=False)
                .str.replace(r"^\((.*)\)$", r"-\1", regex=True))
    return pd.to_numeric(cleaned, errors="coerce")


# --------------------------------------------------------------- press releases
def press_release_index(persist: bool = True) -> pd.DataFrame:
    """
    Every press release currently on RBI's listing page, with its prid and PDF.

    Why it matters: RBI publishes daily Money Market Operations, every LAF/VRRR
    auction result, and the WSS extract through this feed. Polling it nightly is
    the cheapest complete view of RBI's operational actions, and it is the index
    the other functions here resolve against.

    NOTE: the page shows only the most recent ~2 weeks (83 releases when
    verified). Deeper history needs the year/month archive pages, which are not
    wired up yet.
    """
    f = _session()
    r = f.get(PRESS_INDEX)
    save_raw("rbi", "press_release_index", r.content, "html", {"url": PRESS_INDEX})

    # Walk the document in order so each release keeps ITS own date and PDF.
    # (Collecting all PDF hrefs separately and zipping them against the rows
    # misaligns them wherever a release has no PDF.)
    html_text = r.text
    rows, current_date = [], None
    for m in re.finditer(
        r"class=\"tableheader\"[^>]*>\s*<b>\s*(?P<date>[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})"
        r"|<a\s+class='link2'\s+href=BS_PressReleaseDisplay\.aspx\?prid=(?P<prid>\d+)>(?P<title>.*?)</a>"
        r"|(?P<pdf>https://rbidocs\.rbi\.org\.in/rdocs/PressRelease/PDFs/[\w.]+\.PDF)",
        html_text, re.I | re.S,
    ):
        if m.group("date"):
            current_date = _text(m.group("date"))
        elif m.group("prid"):
            rows.append({"prid": m.group("prid"), "release_date": current_date,
                         "title": _text(m.group("title")), "pdf_url": None})
        elif m.group("pdf") and rows and rows[-1]["pdf_url"] is None:
            rows[-1]["pdf_url"] = m.group("pdf")

    if not rows:
        raise RuntimeError(
            "press_release_index parsed 0 releases. The listing markup changed "
            f"(expected \"<a class='link2' href=...prid=N>\"). Bytes archived: {len(r.content)}"
        )

    df = pd.DataFrame(rows)
    df["index_fetched_at"] = datetime.now().isoformat(timespec="seconds")

    if persist:
        write_table(df, "rbi", "press_release_index")
    return df


def _fetch_press_release(prid: str) -> str:
    """Fetch one press release, archiving the bytes before anything parses them."""
    f = _session()
    url = PRESS_ITEM.format(prid=prid)
    r = f.get(url)
    save_raw("rbi", "press_release", r.content, "html", {"url": url, "prid": prid})
    return r.text


def _latest_prid(pattern: str, index: pd.DataFrame | None = None) -> tuple[str, str]:
    """Newest release whose title matches. Raises if absent rather than guessing."""
    idx = index if index is not None else press_release_index(persist=False)
    hits = idx[idx["title"].str.contains(pattern, case=False, regex=True)]
    if hits.empty:
        raise RuntimeError(
            f"No press release matching {pattern!r} on the current listing page. "
            "It may have aged off the front page, or the title wording changed."
        )
    row = hits.iloc[0]
    return str(row["prid"]), str(row["title"])


def _parse_all_tables(html_text: str, source_desc: str) -> pd.DataFrame:
    tables = pd.read_html(io.StringIO(html_text))
    frames = [tidy_table(t, i) for i, t in enumerate(tables)]
    frames = [d for d in frames if not d.empty]
    if not frames:
        raise RuntimeError(f"{source_desc}: found {len(tables)} tables but none yielded rows.")
    out = pd.concat(frames, ignore_index=True)
    out["value_num"] = _numeric(out["value"])
    return out


def wss_extract(prid: str | None = None, persist: bool = True) -> pd.DataFrame:
    """
    Weekly Statistical Supplement -- RBI's highest-frequency statistical release
    (every Friday), taken from its press-release rendering so no __VIEWSTATE
    round-trip is needed.

    Why it matters for research: this single document carries
      Table 4  Scheduled Commercial Banks -- aggregate deposits and BANK CREDIT,
               with fortnightly, financial-year and year-on-year variation plus
               growth percentages, and the food / non-food credit split
      Table 6  Liquidity Operations by RBI -- daily Repo, Reverse Repo, VRR,
               VRRR, MSF, SDF, OMO and NET INJECTION/ABSORPTION
      Table 5  Money Stock components and sources
      Table 3  Forex reserves (cross-checks the DBIE gateway series)

    Bank credit growth is the transmission channel from RBI policy to corporate
    earnings; net liquidity absorption is the systemic-liquidity signal that
    drives short-rate carry. Both are inputs to a policy-surprise series.
    """
    if prid is None:
        prid, title = _latest_prid(r"Weekly Statistical Supplement")
    else:
        title = f"prid={prid}"

    html_text = _fetch_press_release(prid)
    out = _parse_all_tables(html_text, f"wss_extract({prid})")
    out["prid"] = prid
    out["release_title"] = title

    if persist:
        write_table(out, "rbi", "wss_extract")
    return out


def money_market_operations(prid: str | None = None, persist: bool = True) -> pd.DataFrame:
    """
    Daily Money Market Operations -- overnight call, notice, term money, CBLO
    and repo volumes with weighted-average rates.

    Why it matters for research: the spread between the weighted-average call
    rate and the policy repo rate is the cleanest daily read on whether the
    banking system is actually short of liquidity, independent of what the
    policy rate says. It leads bank NIM pressure and short-end bond moves.
    """
    if prid is None:
        prid, title = _latest_prid(r"Money Market Operations")
    else:
        title = f"prid={prid}"

    html_text = _fetch_press_release(prid)
    out = _parse_all_tables(html_text, f"money_market_operations({prid})")
    out["prid"] = prid
    out["release_title"] = title

    if persist:
        write_table(out, "rbi", "money_market_operations")
    return out


# --------------------------------------------------------------- policy rates
POLICY_RATE_BLOCK_RE = re.compile(r"Policy&nbsp;\s*Rates.*?</div>", re.S | re.I)
RATE_ROW_RE = re.compile(r"<th>\s*(.*?)\s*</th>\s*<td[^>]*>\s*:\s*(.*?)\s*</td>", re.S)


def policy_rates(persist: bool = True) -> pd.DataFrame:
    """
    Current policy rates from the RBI homepage -- the authoritative snapshot.

    Why it matters for research: the policy repo rate anchors every discount
    rate in the book, and the SDF/MSF corridor bounds the overnight rate. Paired
    with the daily weighted-average call rate from money_market_operations(),
    the gap to the corridor is the systemic-liquidity signal; paired with
    consensus, rate changes become a policy-surprise series.

    This is a SNAPSHOT, not a history. Archived daily it accumulates into a
    genuine point-in-time rate history -- which is exactly why the daily archive
    should start before the rest of the pipeline is finished. For history prior
    to today, see nsdp_banking_ratios() (caveated) or the Handbook of Statistics.
    """
    f = _session()
    r = f.get(BASE + "/")
    save_raw("rbi", "policy_rates", r.content, "html", {"url": BASE + "/"})

    block = POLICY_RATE_BLOCK_RE.search(r.text)
    if not block:
        raise RuntimeError(
            "policy_rates: the homepage 'Policy Rates' block was not found. "
            "The accordion markup changed -- re-inspect the homepage."
        )

    rows = []
    for label, value in RATE_ROW_RE.findall(block.group(0)):
        label = _text(label.replace("&nbsp;", " "))
        value = _text(value)
        if not label:
            continue
        rows.append({
            "rate_name": label,
            "rate_pct": pd.to_numeric(value.rstrip("%"), errors="coerce"),
            "as_reported": value,
        })

    if not rows:
        raise RuntimeError("policy_rates: found the block but parsed no rate rows.")

    df = pd.DataFrame(rows)
    df["observed_at"] = datetime.now().isoformat(timespec="seconds")
    if persist:
        write_table(df, "rbi", "policy_rates")
    return df


def nsdp_banking_ratios(persist: bool = True) -> pd.DataFrame:
    """
    Fortnightly banking ratios from RBI's National Summary Data Page:
    cash-deposit, credit-deposit and incremental credit-deposit ratios.

    Why it matters for research: the credit-deposit ratio is the standard gauge
    of how stretched bank balance sheets are. A rising CD ratio with slowing
    deposit growth is the classic precursor to funding-cost pressure on NIMs.

    CAVEAT, VERIFIED NOT ASSUMED: this page's header uses colspans that
    read_html expands into DUPLICATE period columns (2025/Jul. 25 twice, and so
    on). For the ratio rows the duplicated values agree, so they are usable. For
    the policy-rate rows on the same page they DO NOT agree, meaning the header
    is offset relative to those columns -- cross-checking against the homepage
    showed the repo rate is 5.25% while this page's first column reads 5.50%
    (the MSF/Bank Rate value). Policy rates are therefore deliberately EXCLUDED
    here; use policy_rates() for those. Do not "fix" this by trusting the
    alignment -- capture a fresh cURL or read the Handbook instead.
    """
    f = _session()
    r = f.get(NSDP_RATES)
    save_raw("rbi", "nsdp_banking_ratios", r.content, "html", {"url": NSDP_RATES})

    out = _parse_all_tables(r.text, "nsdp_banking_ratios")
    ratios = out[out["row_label"].str.contains(r"ratio", case=False, regex=True)].copy()
    if ratios.empty:
        raise RuntimeError(
            "nsdp_banking_ratios: page parsed but no ratio rows matched. "
            "Check whether the row labels changed."
        )
    if persist:
        write_table(ratios, "rbi", "nsdp_banking_ratios")
    return ratios


# --------------------------------------------------------------- back-compat
def fetch_publication(key: str) -> str:
    """Archive a publication landing page verbatim. Used by run_daily's rbi_wss job."""
    if key not in PUBLICATIONS:
        raise KeyError(f"unknown publication {key!r}. Known: {', '.join(PUBLICATIONS)}")
    url = PUBLICATIONS[key]
    r = _session().get(url)
    save_raw("rbi", f"pub_{key}", r.content, "html", {"url": url})
    return r.text


if __name__ == "__main__":
    idx = press_release_index(persist=False)
    print(f"press releases on front page: {len(idx)}\n")
    for fn in (wss_extract, money_market_operations, policy_rates, nsdp_banking_ratios):
        df = fn(persist=False)
        print(f"--- {fn.__name__}: {len(df)} rows")
        print(df.head(5).to_string(max_colwidth=40))
        print()
