"""
DeepSeek terse-synthesis layer -- the ONLY place an LLM appears in this
engine.

composite_score is computed deterministically in `_composite_score()` BEFORE
any LLM call and is never handed to the model to reproduce or approve --
DeepSeek's sole job is to phrase a short flag for the already-ranked
shortlist. This is a deliberate refinement over asking the model to also
echo the score (which the original design sketch considered): a number we
already know exactly has nothing to gain from being validated against an
LLM's opinion of it, and it removes a whole retry/failure path for no
benefit. Minimality of the model's actual output (the flag) is still
enforced technically, not just by prompt wording -- see core.llm.call_tool's
forced tool_choice and `_validate_and_truncate()` below.
"""
from __future__ import annotations

import pandas as pd

from analytics.watchlist import WATCHLIST_SYMBOLS
from core.asof import latest_per
from core.llm import call_tool, deepseek_client
from core.storage import db, register_views, write_table

SHORTLIST_SIZE = 25
FLAG_MAX_CHARS = 120
MAX_CONTRIBUTING_SIGNALS = 5

DIGEST_TOOL = {
    "name": "emit_digest",
    "description": "Return a terse one-line flag for this instrument. No prose, no explanation.",
    "input_schema": {
        "type": "object",
        "properties": {
            "instrument": {"type": "string"},
            "flag": {
                "type": "string",
                "description": f"One line, HARD CAP {FLAG_MAX_CHARS} characters. No paragraphs.",
            },
            "contributing_signals": {
                "type": "array", "items": {"type": "string"},
                "description": "Up to 5 short tokens, e.g. 'momentum:+2.1sigma'.",
            },
        },
        "required": ["instrument", "flag", "contributing_signals"],
    },
}

# Hand-set, documented Phase 1 starting point -- NOT backtested or tuned.
# Revisit once enough history has accumulated to validate these weights.
COMPOSITE_WEIGHTS = {
    "momentum_z": 0.4,
    "beta_deviation_z": 0.3,
    "event_z": 0.3,
}


def _registered_views(con) -> set[str]:
    return set(con.execute("SELECT table_name FROM information_schema.tables").df()["table_name"])


def _fetch_latest_signals() -> pd.DataFrame:
    con = db(read_only=True)
    try:
        register_views(con)
        views = _registered_views(con)
        required = {"derived_capm_beta", "derived_momentum_zscore"}
        missing = required - views
        if missing:
            raise RuntimeError(
                f"run_digest needs {sorted(missing)} to exist first -- run the "
                "analytics_capm_beta and analytics_momentum jobs before analytics_digest."
            )

        # write_table() never overwrites -- a rerun leaves a second vintage
        # that can share the same as_of_date as the first, so filtering on
        # "as_of_date = max(as_of_date)" alone does not dedupe and can
        # silently double a symbol's row.
        #
        # latest_per() collapses to the latest vintage per symbol regardless of
        # how many same-day vintages exist, and does it ROW-wise: the previous
        # arg_max-per-column form could resolve beta and alpha from different
        # rows, yielding a pair no single CAPM fit ever produced.
        beta_latest = f"({latest_per('derived_capm_beta', ['symbol'], columns='symbol, beta, alpha, as_of_date')})"
        momentum_latest = f"({latest_per('derived_momentum_zscore', ['symbol'], columns='symbol, momentum_zscore, as_of_date')})"

        event_join, event_select = "", "0.0 AS event_zscore"
        if "derived_bulk_block_anomaly" in views:
            event_join = f"""
                LEFT JOIN ({latest_per('derived_bulk_block_anomaly', ['symbol'],
                                       columns='symbol, notional_zscore')}) e USING (symbol)
            """
            event_select = "COALESCE(e.notional_zscore, 0.0) AS event_zscore"

        return con.execute(f"""
            SELECT b.symbol, b.beta, b.alpha, m.momentum_zscore, m.as_of_date,
                   {event_select}
            FROM {beta_latest} b
            JOIN {momentum_latest} m USING (symbol)
            {event_join}
        """).df()
    finally:
        con.close()


def _composite_score(df: pd.DataFrame) -> pd.DataFrame:
    """
    Deterministic weighted sum of already-z-scored signals, clipped to
    [-1, 1]. The ranking below is 100% Python math -- the LLM call only
    phrases the flag for whatever this function already decided matters.
    """
    df = df.copy()
    beta_std = df["beta"].std()
    beta_z = (df["beta"] - df["beta"].mean()) / beta_std if beta_std else pd.Series(0.0, index=df.index)
    momentum_z = df["momentum_zscore"].fillna(0.0)
    event_z = df.get("event_zscore", pd.Series(0.0, index=df.index)).fillna(0.0)
    raw = (COMPOSITE_WEIGHTS["momentum_z"] * momentum_z
           + COMPOSITE_WEIGHTS["beta_deviation_z"] * beta_z
           + COMPOSITE_WEIGHTS["event_z"] * event_z)
    df["composite_score"] = raw.clip(-1.0, 1.0)
    return df


def _select_two_tiers(scored: pd.DataFrame, shortlist_size: int) -> pd.DataFrame:
    """
    Watchlist tier: every WATCHLIST_SYMBOLS member with a signal, regardless
    of |composite_score| -- shown because it matters MECHANICALLY (this
    engine prices NIFTY/BANKNIFTY options against these levels; a
    heavyweight's move is a real input even when nothing statistically
    unusual is happening to its own beta/momentum), not because it's
    statistically extreme.

    Broad scan tier: the existing top-N by |composite_score|, with watchlist
    members excluded so a name is never narrated twice under two different
    framings. This tier answers a genuinely different question than the
    watchlist -- "what's statistically unusual right now", not "what
    matters" -- and is labeled that way in the dashboard.
    """
    scored = scored.copy()
    scored["tier"] = scored["symbol"].apply(
        lambda s: "watchlist" if s in WATCHLIST_SYMBOLS else "broad_scan")
    watchlist = scored[scored["tier"] == "watchlist"]
    broad_scan = scored[scored["tier"] == "broad_scan"]
    broad_scan = broad_scan.loc[
        broad_scan["composite_score"].abs().sort_values(ascending=False).index
    ].head(shortlist_size)
    return pd.concat([watchlist, broad_scan], ignore_index=True)


def _validate_and_truncate(out: dict) -> dict:
    if "instrument" not in out:
        raise ValueError(f"emit_digest response missing 'instrument': {out}")
    flag = str(out.get("flag", ""))
    if len(flag) > FLAG_MAX_CHARS:
        flag = flag[: FLAG_MAX_CHARS - 3].rstrip() + "..."
    signals = list(out.get("contributing_signals") or [])[:MAX_CONTRIBUTING_SIGNALS]
    return {"instrument": out["instrument"], "flag": flag, "contributing_signals": signals}


def run_digest(shortlist_size: int = SHORTLIST_SIZE, persist: bool = True) -> pd.DataFrame:
    """
    1) Compute composite_score deterministically for every symbol with an
       overlapping beta+momentum signal.
    2) Split into two tiers (see _select_two_tiers): Watchlist (every
       WATCHLIST_SYMBOLS member, shown regardless of |composite_score|
       because it's mechanically important) and Broad scan (top/bottom
       `shortlist_size` by |composite_score|, excluding watchlist members).
       Calling DeepSeek once per symbol across the whole universe (~2,000
       names) would make narration the bottleneck for a component whose job
       is to compress an already-computed ranking, not to produce one.
    3) One forced tool_choice call per selected symbol; validate/truncate
       the response; attach the (already-known) composite_score and tier in
       Python; write_table the result.
    """
    signals = _fetch_latest_signals()
    if signals.empty:
        raise RuntimeError("run_digest: no overlapping beta/momentum signals available yet.")
    scored = _composite_score(signals)
    shortlist = _select_two_tiers(scored, shortlist_size)

    client = deepseek_client()
    rows = []
    for _, r in shortlist.iterrows():
        prompt = (
            f"Instrument: {r['symbol']} (tier: {r['tier']})\n"
            f"composite_score: {r['composite_score']:.3f} (range -1..1, already computed -- "
            "do not recompute it)\n"
            f"beta: {r['beta']:.3f}\n"
            f"momentum_zscore: {r['momentum_zscore']:.3f}\n"
            f"event_zscore: {r.get('event_zscore', 0.0):.3f}\n"
            "If tier is 'watchlist', this instrument is being shown because it's a major "
            "index constituent, not because its score is extreme -- a near-zero score here "
            "is normal and worth saying plainly (e.g. 'quiet, no unusual signal'), not "
            "dressed up as a call. Return ONLY the emit_digest tool call: a terse one-line "
            "flag summarizing these numbers, plus up to 5 short contributing-signal tokens."
        )
        raw_out = call_tool(client, DIGEST_TOOL, prompt)
        out = _validate_and_truncate(raw_out)
        rows.append({
            "instrument": r["symbol"], "composite_score": float(r["composite_score"]),
            "flag": out["flag"], "contributing_signals": out["contributing_signals"],
            "as_of_date": r["as_of_date"], "tier": r["tier"],
        })

    df = pd.DataFrame(rows)
    if persist and not df.empty:
        write_table(df, "derived", "digest")
    return df
