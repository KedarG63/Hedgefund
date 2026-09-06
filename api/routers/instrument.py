"""
Everything scoped to one instrument: price, signals, events, fundamentals.

Every route takes ?as_of= and reads through core.asof, so a back-dated request
returns the prices AND the derived signals as they stood on that date. That is
the property the whole migration is built around; it is not a per-route
nicety.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
from fastapi import APIRouter, Query

from api.deps import AsOf, cursor, require_view, views
from api.responses import frame
from api.serializers import fundamentals as fx

router = APIRouter(prefix="/api/instrument", tags=["instrument"])

# nse_bhavcopy carries CM and F&O in one table; the equity cash series is
# SctySrs = 'EQ'. Column names are NSE's UDiFF schema, not the legacy one.
EQ = "SctySrs = 'EQ'"


def _sql_quote(value: str) -> str:
    """Single-quote a literal for inlining. Symbols come from a path segment,
    so they are untrusted even though the router's pattern is narrow."""
    return "'" + value.replace("'", "''") + "'"


@router.get("/{symbol}/price")
def price(symbol: str, as_of: AsOf = None, limit: int = Query(2000, le=20000),
          fmt: str = "arrow"):
    """Daily OHLCV for the equity series, newest last, with delivery % where known."""
    from core.asof import asof_sql

    require_view("nse_bhavcopy")
    sym = _sql_quote(symbol.upper())
    px = asof_sql(
        "nse_bhavcopy",
        as_of,
        columns='TradDt AS trade_date, OpnPric AS open, HghPric AS high, '
                'LwPric AS low, ClsPric AS close, PrvsClsgPric AS prev_close, '
                'TtlTradgVol AS volume, TtlTrfVal AS turnover, '
                'TtlNbOfTxsExctd AS trades',
        where=f"{EQ} AND TckrSymb = {sym}",
    )

    if "nse_bhavcopy_delivery" in views():
        dlv = asof_sql(
            "nse_bhavcopy_delivery", as_of,
            columns='DATE1 AS trade_date, DELIV_QTY AS delivered_qty, '
                    'DELIV_PER AS delivered_pct',
            where=f"SERIES = 'EQ' AND SYMBOL = {sym}",
        )
        sql = (f"SELECT p.*, d.delivered_qty, d.delivered_pct FROM ({px}) p "
               f"LEFT JOIN ({dlv}) d USING (trade_date) "
               f"ORDER BY trade_date LIMIT {int(limit)}")
    else:
        sql = f"SELECT * FROM ({px}) ORDER BY trade_date LIMIT {int(limit)}"

    return frame(cursor().execute(sql), fmt, {"symbol": symbol.upper()})


# Each entry: view, the value column, and what to call the signal. Kept as data
# so a new derived_* table becomes one line here rather than another branch.
SIGNALS: list[tuple[str, str, str, str]] = [
    ("derived_capm_beta",             "beta",            "CAPM beta",        "r_squared"),
    ("derived_momentum_zscore",       "momentum_zscore", "Momentum z",       "trailing_return"),
    ("derived_low_vol_factor",        "low_vol_score",   "Low volatility",   "realized_vol"),
    ("derived_size_factor",           "size_score",      "Size",             "market_cap_inr_cr"),
    ("derived_variance_ratio",        "variance_ratio",  "Variance ratio",   "z_stat"),
    ("derived_factor_model",          "composite_score", "Factor composite", "momentum_zscore"),
    ("derived_multivariate_outliers", "anomaly_score_raw", "Anomaly score",  "is_outlier"),
]


@router.get("/{symbol}/signals")
def signals(symbol: str, as_of: AsOf = None, fmt: str = "arrow"):
    """
    Every derived signal for one symbol, one row each, in long form.

    Long rather than wide on purpose: the panel renders label/value/context
    rows, and a wide row would have to change shape every time a signal is
    added. Missing signals are simply absent rather than null columns.
    """
    from core.asof import latest_per

    sym = _sql_quote(symbol.upper())
    parts = []
    for view, value_col, label, context_col in SIGNALS:
        if view not in views():
            continue
        inner = latest_per(view, ["symbol"], as_of,
                           columns=f"symbol, {value_col}, {context_col}, as_of_date",
                           where=f"symbol = {sym}")
        parts.append(
            f"SELECT {_sql_quote(label)} AS signal, "
            f"CAST({value_col} AS DOUBLE) AS value, "
            f"{_sql_quote(context_col)} AS context_name, "
            f"CAST({context_col} AS VARCHAR) AS context_value, "
            f"as_of_date FROM ({inner})"
        )
    if not parts:
        return frame(pd.DataFrame(columns=["signal", "value", "context_name",
                                           "context_value", "as_of_date"]), fmt)
    return frame(cursor().execute(" UNION ALL ".join(parts)), fmt,
                 {"symbol": symbol.upper()})


@router.get("/{symbol}/events")
def events(symbol: str, as_of: AsOf = None, limit: int = Query(200, le=2000),
           fmt: str = "arrow"):
    """
    Announcements, insider filings and bulk/block deals as one typed stream.

    Shape is uniform -- {ts, kind, headline, detail} -- because the panel is a
    single reverse-chronological tape and these become the markers drawn on
    the price pane. Only symbol-keyed sources are unioned here; rating actions
    key on company name and need the ISIN bridge, so they are served
    separately by /api/credit rather than name-matched on the fly.
    """
    from core.asof import asof_sql

    sym = _sql_quote(symbol.upper())
    parts = []

    if "nse_announcements" in views():
        parts.append(f"""SELECT sort_date AS ts, 'announcement' AS kind,
            "desc" AS headline, attchmntText AS detail
            FROM ({asof_sql("nse_announcements", as_of,
                            columns='sort_date, "desc", attchmntText, symbol',
                            where=f"symbol = {sym}")})""")

    if "nse_insider_trading" in views():
        parts.append(f"""SELECT broadcastDateTime AS ts, 'insider' AS kind,
            regulation AS headline, typeOfSubmission AS detail
            FROM ({asof_sql("nse_insider_trading", as_of,
                            columns="broadcastDateTime, regulation, typeOfSubmission, symbol",
                            where=f"symbol = {sym}")})""")

    for view, kind in (("nse_bulk_deals", "bulk deal"), ("nse_block_deals", "block deal")):
        if view in views():
            parts.append(f"""SELECT BD_DT_DATE AS ts, {_sql_quote(kind)} AS kind,
                BD_CLIENT_NAME || ' ' || BD_BUY_SELL AS headline,
                'qty ' || CAST(BD_QTY_TRD AS VARCHAR) || ' @ ' || CAST(BD_TP_WATP AS VARCHAR) AS detail
                FROM ({asof_sql(view, as_of,
                                columns="BD_DT_DATE, BD_CLIENT_NAME, BD_BUY_SELL, "
                                        "BD_QTY_TRD, BD_TP_WATP, BD_SYMBOL",
                                where=f"BD_SYMBOL = {sym}")})""")

    if not parts:
        return frame(pd.DataFrame(columns=["ts", "kind", "headline", "detail"]), fmt)

    sql = (f"SELECT * FROM ({' UNION ALL '.join(parts)}) "
           f"ORDER BY ts DESC LIMIT {int(limit)}")
    return frame(cursor().execute(sql), fmt, {"symbol": symbol.upper()})


@router.get("/{symbol}/options")
def options(symbol: str, expiry: str | None = None, as_of: AsOf = None,
            fmt: str = "arrow"):
    """
    Option chain with solved Greeks, one row per strike.

    CALLS and PUTS arrive as separate rows keyed on option_type; this pivots
    them onto one row per strike so the panel can render the conventional
    ladder -- calls left, strike centre, puts right -- which is how the skew is
    actually read.

    iv_divergence_vs_nse is carried through because it is the interesting
    column: where our Black-Scholes solve disagrees with the exchange's
    published IV, one of the two is using a different underlying or rate.
    """
    from core.asof import latest_per

    require_view("nse_optchain")
    sym = _sql_quote(symbol.upper())
    where = f"symbol = {sym}"
    if expiry:
        where += f" AND expiry_date = {_sql_quote(expiry)}"

    chain = latest_per(
        "nse_optchain", ["symbol", "expiry_date", "strike_price", "option_type"], as_of,
        columns="symbol, expiry_date, strike_price, option_type, open_interest, "
                "change_in_oi, total_traded_volume, implied_volatility_nse, "
                "last_price, bid_price, ask_price, underlying_value",
        where=where,
    )

    greeks_join = ""
    greek_cols = ""
    if "derived_option_greeks" in views():
        g = latest_per(
            "derived_option_greeks",
            ["symbol", "strike_price", "option_type", "expiry_date"], as_of,
            columns="symbol, expiry_date, strike_price, option_type, nse_delta, "
                    "nse_gamma, nse_vega, nse_theta, implied_volatility_solved, "
                    "iv_divergence_vs_nse",
            where=where,
        )
        greeks_join = (f"LEFT JOIN ({g}) g "
                       f"USING (symbol, expiry_date, strike_price, option_type)")
        greek_cols = (", g.nse_delta, g.nse_gamma, g.nse_vega, g.nse_theta, "
                      "g.implied_volatility_solved, g.iv_divergence_vs_nse")

    joined = f"SELECT c.*{greek_cols} FROM ({chain}) c {greeks_join}"

    # One row per strike, calls and puts side by side.
    sql = f"""
        SELECT expiry_date, strike_price, any_value(underlying_value) AS underlying,
               max(CASE WHEN option_type = 'CE' THEN open_interest END) AS call_oi,
               max(CASE WHEN option_type = 'CE' THEN change_in_oi END) AS call_oi_chg,
               max(CASE WHEN option_type = 'CE' THEN last_price END) AS call_ltp,
               max(CASE WHEN option_type = 'CE' THEN implied_volatility_nse END) AS call_iv,
               max(CASE WHEN option_type = 'CE' THEN nse_delta END) AS call_delta,
               max(CASE WHEN option_type = 'PE' THEN open_interest END) AS put_oi,
               max(CASE WHEN option_type = 'PE' THEN change_in_oi END) AS put_oi_chg,
               max(CASE WHEN option_type = 'PE' THEN last_price END) AS put_ltp,
               max(CASE WHEN option_type = 'PE' THEN implied_volatility_nse END) AS put_iv,
               max(CASE WHEN option_type = 'PE' THEN nse_delta END) AS put_delta
        FROM ({joined})
        GROUP BY expiry_date, strike_price
        ORDER BY expiry_date, strike_price
    """
    return frame(cursor().execute(sql), fmt, {"symbol": symbol.upper()})


@router.get("/{symbol}/fundamentals")
def fundamentals(symbol: str, statement: str = Query("income", pattern="^(income|balance|cash)$"),
                 consolidated: str | None = None, as_of: AsOf = None, fmt: str = "arrow"):
    """
    Canonical income statement / balance sheet / cash flow.

    The assembly lives in api.serializers.fundamentals -- the same code the
    Streamlit dashboard calls, which is the entire point of having extracted
    it in Phase 0. This route only fetches the facts and picks a statement.

    screener.in is not fetched here: it is a live per-company scrape behind a
    session cookie, so it stays an explicit user action in the dashboard
    rather than something an endpoint does on every request. XBRL alone is
    what this returns.

    latest_vintage(), not asof_sql(): nse_xbrl_facts has no natural key and is
    deliberately undeclared -- an Ind-AS filer can tag opening and closing cash
    under one (source_url, context_id, concept), identical in every
    descriptive column and differing only in value. So this selects the newest
    scrape OF EACH FILING rather than trying to collapse individual facts.
    """
    from core.asof import latest_vintage

    require_view("nse_xbrl_facts")
    sym = _sql_quote(symbol.upper())
    where = f"symbol = {sym}"
    if consolidated:
        where += f" AND consolidated = {_sql_quote(consolidated)}"
    facts = cursor().execute(
        latest_vintage("nse_xbrl_facts", ["source_url"], as_of, where=where)).df()

    builder = {"income": fx.income_statement, "balance": fx.balance_sheet,
               "cash": fx.cash_flow}[statement]
    table = builder(facts)
    if table.empty:
        return frame(pd.DataFrame(), fmt, {"symbol": symbol.upper(), "statement": statement})

    # The serializer returns line items as the INDEX and periods as columns.
    # Reset it into a column so the wire format stays a plain table -- Arrow
    # has no index concept, and a client should not have to reconstruct one.
    out = table.reset_index().rename(columns={"index": "line_item"})
    return frame(out, fmt, {"symbol": symbol.upper(), "statement": statement,
                            "periods": len(table.columns)})
