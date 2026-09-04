"""
Event-driven signals built from what's actually parsed in the NSE tables
today, with real limitations stated rather than smoothed over.

All three date-bearing source columns here (nse_insider_trading.
broadcastDateTime, nse_bulk_deals/nse_block_deals.BD_DT_DATE,
nse_shareholding_pattern.date) are text like "22-Aug-2026 17:50:32" /
"18-AUG-2026" / "30-JUN-2026" -- parsed explicitly with pd.to_datetime and an
exact format string. Sorting these as raw strings would put "18-AUG-2026"
before "30-JUN-2026" alphabetically, the same trap connectors.commodities.py
documents for MCX's ExpiryDate.
"""
from __future__ import annotations

import pandas as pd

from core.storage import db, register_views, write_table


def _query(sql: str) -> pd.DataFrame:
    con = db(read_only=True)
    try:
        register_views(con)
        return con.execute(sql).df()
    finally:
        con.close()


def insider_filing_frequency(window_days: int = 30, persist: bool = True) -> pd.DataFrame:
    """
    COUNT of PIT disclosure filings per symbol over the trailing window.

    NOT value-weighted cluster-buy detection like the SEC insider module --
    nse_insider_trading carries filing METADATA only (company, regulation
    clause, XBRL/ixbrl links). The transacted quantity/value/buy-sell flag
    sit inside the linked XBRL attachment, which this connector does not
    parse -- that would be a connectors/ change, out of scope here. This
    signal is filing-frequency clustering, not transaction-value clustering.
    """
    df = _query("SELECT symbol, broadcastDateTime FROM nse_insider_trading")
    if df.empty:
        raise RuntimeError("insider_filing_frequency: nse_insider_trading has no rows yet.")

    df["broadcast_dt"] = pd.to_datetime(df["broadcastDateTime"], format="%d-%b-%Y %H:%M:%S",
                                        errors="coerce")
    df = df.dropna(subset=["broadcast_dt"])
    cutoff = df["broadcast_dt"].max() - pd.Timedelta(days=window_days)
    recent = df[df["broadcast_dt"] >= cutoff]

    out = recent.groupby("symbol").size().reset_index(name="filing_count")
    out["as_of_date"] = df["broadcast_dt"].max().date().isoformat()
    out["window_days"] = window_days
    out = out.sort_values("filing_count", ascending=False, ignore_index=True)
    if persist and not out.empty:
        write_table(out, "derived", "insider_filing_frequency")
    return out


def bulk_block_deal_anomaly(window_days: int = 60, persist: bool = True) -> pd.DataFrame:
    """
    Per-symbol net buy-sell notional per day (BD_QTY_TRD * BD_TP_WATP, signed
    by BD_BUY_SELL), z-scored against that symbol's own trailing history of
    bulk/block notional. Combines nse_bulk_deals and nse_block_deals -- both
    share the same BD_-prefixed schema. Degrades gracefully if either view
    is missing (a dataset directory with no parquet files never registers a
    view, so a not-yet-populated source is caught here and skipped, not
    assumed to exist).
    """
    frames = []
    for view in ("nse_bulk_deals", "nse_block_deals"):
        try:
            frames.append(_query(f"SELECT * FROM {view}"))
        except Exception:
            continue
    frames = [f for f in frames if not f.empty]
    if not frames:
        df = pd.DataFrame(columns=["symbol", "deal_date", "net_notional",
                                   "notional_zscore", "n_obs", "as_of_date"])
        if persist:
            write_table(df, "derived", "bulk_block_anomaly")
        return df

    deals = pd.concat(frames, ignore_index=True)
    deals["deal_date"] = pd.to_datetime(deals["BD_DT_DATE"], format="%d-%b-%Y", errors="coerce")
    deals = deals.dropna(subset=["deal_date"])
    deals["notional"] = pd.to_numeric(deals["BD_QTY_TRD"], errors="coerce") * \
        pd.to_numeric(deals["BD_TP_WATP"], errors="coerce")
    deals["signed_notional"] = deals["notional"] * deals["BD_BUY_SELL"].str.upper().map(
        {"BUY": 1, "SELL": -1}).fillna(0)

    daily = deals.groupby(["BD_SYMBOL", "deal_date"], as_index=False)["signed_notional"].sum()
    daily = daily.rename(columns={"BD_SYMBOL": "symbol", "signed_notional": "net_notional"})

    rows = []
    for symbol, grp in daily.groupby("symbol"):
        grp = grp.sort_values("deal_date").tail(window_days)
        mean_, std_ = grp["net_notional"].mean(), grp["net_notional"].std()
        latest = grp.iloc[-1]
        z = (latest["net_notional"] - mean_) / std_ if std_ else 0.0
        rows.append({
            "symbol": symbol, "deal_date": latest["deal_date"].date().isoformat(),
            "net_notional": latest["net_notional"], "notional_zscore": z,
            "n_obs": len(grp), "as_of_date": latest["deal_date"].date().isoformat(),
        })
    df = pd.DataFrame(rows)
    if persist:
        write_table(df, "derived", "bulk_block_anomaly")
    return df


def fii_dii_divergence(persist: bool = True) -> pd.DataFrame:
    """
    Day-over-day net positioning direction from nse_participant_oi (Total
    Long - Total Short Contracts) for FII vs DII: a divergence (one net
    long-leaning, the other net short-leaning) is a crowding/positioning
    signal -- per the connector's own docstring, treat this as a crowding
    factor, not a directional call.
    """
    df = _query("""
        SELECT trade_date, "Client Type" AS client_type,
               "Total Long Contracts" - "Total Short Contracts" AS net_contracts
        FROM nse_participant_oi
        WHERE "Client Type" IN ('FII', 'DII')
        ORDER BY trade_date
    """)
    if df.empty:
        raise RuntimeError("fii_dii_divergence: nse_participant_oi has no FII/DII rows yet.")

    panel = df.pivot(index="trade_date", columns="client_type", values="net_contracts")
    if "FII" not in panel or "DII" not in panel:
        raise RuntimeError("fii_dii_divergence: need both FII and DII rows for the same date.")
    panel["fii_direction"] = panel["FII"].apply(lambda v: 1 if v > 0 else (-1 if v < 0 else 0))
    panel["dii_direction"] = panel["DII"].apply(lambda v: 1 if v > 0 else (-1 if v < 0 else 0))
    panel["divergence_flag"] = panel["fii_direction"] != panel["dii_direction"]
    out = panel.reset_index().rename(columns={"FII": "fii_net_contracts", "DII": "dii_net_contracts"})
    if persist:
        write_table(out, "derived", "fii_dii_divergence")
    return out


def promoter_holding_change(persist: bool = True) -> pd.DataFrame:
    """
    Quarter-over-quarter change in pr_and_prgrp (promoter + promoter-group
    %) per symbol -- a falling pr_and_prgrp between consecutive quarters is
    the signal. nse_shareholding_pattern has no pledge-specific field --
    pledge detail is explicitly out of scope, matching the connector's own
    docstring caveat.
    """
    df = _query("""
        SELECT symbol, date, pr_and_prgrp FROM nse_shareholding_pattern
        WHERE pr_and_prgrp IS NOT NULL
    """)
    if df.empty:
        raise RuntimeError("promoter_holding_change: nse_shareholding_pattern has no rows yet.")

    df["quarter_end"] = pd.to_datetime(df["date"], format="%d-%b-%Y", errors="coerce")
    df["pr_and_prgrp"] = pd.to_numeric(df["pr_and_prgrp"], errors="coerce")
    df = df.dropna(subset=["quarter_end", "pr_and_prgrp"]).sort_values(["symbol", "quarter_end"])
    df["prev_pr_and_prgrp"] = df.groupby("symbol")["pr_and_prgrp"].shift(1)
    df["change_pct_points"] = df["pr_and_prgrp"] - df["prev_pr_and_prgrp"]

    latest = df.dropna(subset=["prev_pr_and_prgrp"]).groupby("symbol").tail(1).copy()
    latest["quarter_end"] = latest["quarter_end"].dt.date.astype(str)
    if persist and not latest.empty:
        write_table(latest, "derived", "promoter_holding_change")
    return latest
