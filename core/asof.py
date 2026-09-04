"""
Point-in-time reads: the latest vintage of each row, as it was known on a date.

WHAT THIS REPLACES
    The vintage-collapse pattern was hand-written at a dozen call sites, each
    with its own PARTITION BY, spread across dashboard/app.py and analytics/.
    Every one is correct in isolation; collectively they were an undeclared
    schema living in query strings, and none of them could answer "what did we
    know on 2026-06-30" because none bounded knowledge_date.

    asof_sql() does both jobs from one declared key, so adding the bound is
    free everywhere instead of twelve separate edits.

WHY A ROW NEEDS COLLAPSING AT ALL
    write_table() never overwrites -- that is deliberate, it is what makes the
    warehouse append-only and auditable. The cost is that a rerun leaves a
    SECOND vintage of the same logical row, and a plain SELECT returns both.
    Confirmed live: three run_daily.py runs on 2026-08-22 tripled every
    nse_bhavcopy row for that day.

WHY knowledge_date IS NOT ENOUGH TO ORDER VINTAGES
    write_table() stamps knowledge_date with the DATE, so all three of those
    reruns carry '2026-08-22' and ORDER BY knowledge_date DESC has a three-way
    tie -- row_number() then picks an arbitrary winner, and which one can change
    between runs of the same query.

    The tiebreak is the parquet filename. write_table() names files
    YYYYmmddTHHMMSS_ffffff.parquet at microsecond precision precisely so that
    concurrent writes cannot collide, which also means the name sorts in exact
    write order. read_parquet(..., filename=true) exposes it, so
    "latest vintage" becomes a total order rather than a partial one.

    (On today's warehouse the two orderings happen to agree -- the same-day
    reruns produced identical values. That is luck, not a guarantee: a rerun
    that picked up a corrected price would make the untied version
    non-deterministic.)

WHAT THIS IS NOT FOR
    Not every window function over a vintage is a vintage collapse, and
    replacing those with asof_sql() would silently change what they mean:

      analytics/cross_asset_correlation.py picks the FRONT-MONTH contract with
      PARTITION BY trade_date ORDER BY strptime(ExpiryDate) ASC -- a business
      rule about which futures contract to quote, not a dedup.

      The Command Center's policy-repo tile takes the newest observation with
      ORDER BY observed_at DESC -- "latest published rate", not "latest write".

    Those stay as they are. Use asof_sql() where the intent is "one row per
    natural key, latest write wins".
"""
from __future__ import annotations

from datetime import date

from core.quality import DATASET_KEYS, view_paths


class UnregisteredView(KeyError):
    """
    No natural key is declared for this view.

    Deliberately fatal rather than falling back to "no dedup". Connector
    contract rule 4 is the same instinct: an unnoticed wrong answer costs more
    than a loud refusal. A view with no declared key returns duplicate rows
    from every rerun, and nothing about the result looks wrong.
    """


def natural_key(view: str) -> list[str]:
    """The declared row identity for `view`, or raise."""
    keys = DATASET_KEYS.get(view)
    if not keys:
        raise UnregisteredView(
            f"{view!r} has no declared natural key. Add one to "
            f"core.quality.DATASET_KEYS after checking the view's real columns "
            f"AND confirming the key is unique within a single parquet vintage "
            f"-- a key that is not unique silently drops rows here."
        )
    return keys


def _as_of_literal(as_of: date | str | None) -> str | None:
    """Normalise and validate. Returns an ISO string safe to inline, or None."""
    if as_of is None:
        return None
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)          # raises on anything else
    elif not isinstance(as_of, date):
        raise TypeError(f"as_of must be a date or ISO string, got {type(as_of).__name__}")
    return as_of.isoformat()


def asof_sql(view: str, as_of: date | str | None = None, *,
             columns: str = "*", where: str = "", order_by: str = "",
             limit: int | None = None) -> str:
    """
    SQL for `view` collapsed to one row per natural key, as known on `as_of`.

    as_of=None means "everything we know now" and reproduces the behaviour of
    the hand-written QUALIFY blocks this replaces.

    Returns SQL text rather than executing it, so the caller keeps using its
    own connection -- the dashboard's shared cursor, an analytics module's
    short-lived connection, or a future request-scoped one.

    `columns`, `where` and `order_by` are inlined as written and are for
    code-authored fragments only, never user input. `view` is checked against
    the registry (a whitelist) and `as_of` is parsed as a date, so neither can
    carry SQL.

    Reads the parquet files directly instead of the registered view because it
    needs filename=true for the tiebreak, which register_views() does not
    expose. Same files, same rows.
    """
    keys = natural_key(view)
    paths = view_paths()
    if view not in paths:
        raise UnregisteredView(
            f"{view!r} has a declared key but no parquet directory -- nothing "
            f"has been written for it yet. Check `view in register_views(con)` "
            f"before calling, the same way the dashboard already guards its queries."
        )

    src = (f"read_parquet('{paths[view].as_posix()}/*.parquet', "
           f"union_by_name=true, filename=true)")
    partition = ", ".join(f'"{k}"' for k in keys)

    stamp = _as_of_literal(as_of)
    # knowledge_date is written by write_table() as an ISO 'YYYY-MM-DD' string,
    # so a string comparison is a date comparison here.
    kd_bound = f"WHERE knowledge_date <= '{stamp}'" if stamp else ""

    collapsed = f"""SELECT * EXCLUDE (filename)
        FROM {src}
        {kd_bound}
        QUALIFY row_number() OVER (
            PARTITION BY {partition}
            ORDER BY knowledge_date DESC, filename DESC
        ) = 1"""

    sql = f"SELECT {columns} FROM (\n{collapsed}\n    ) AS {view}"
    if where:
        sql += f"\nWHERE {where}"
    if order_by:
        sql += f"\nORDER BY {order_by}"
    if limit is not None:
        sql += f"\nLIMIT {int(limit)}"
    return sql


def latest_per(view: str, per: list[str] | str, as_of: date | str | None = None, *,
               columns: str = "*", where: str = "",
               order_by: str | None = None) -> str:
    """
    One row per `per` from the point-in-time view.

    This is the row-wise replacement for the
    `arg_max(col, knowledge_date) GROUP BY key` idiom that analytics/ used in
    five modules. Same result on today's data (verified against every affected
    table), but it collapses to a ROW rather than per-column.

    That distinction is the reason to prefer it. arg_max() evaluates each
    column independently, so `arg_max(beta, kd)` and `arg_max(alpha, kd)` can
    resolve to DIFFERENT rows -- a per-column vintage mix that produces a
    beta/alpha pair no single run ever computed. It also has to name every
    column by hand, so a column added to the table later is silently dropped
    instead of appearing. analytics/corporate_events.py was arg_max-ing five
    columns that way; dashboard/app.py's credit panel already avoided arg_max
    for exactly this reason ("one missed column away from silently mixing
    vintages").

    `per` is deliberately NOT the natural key -- it is coarser. The natural key
    of derived_capm_beta is (symbol, as_of_date), which keeps one row per
    computation date; a caller that wants the CURRENT beta passes per=["symbol"]
    and gets the single latest row.

    WHICH row that is, is derived from the registry rather than hardcoded. The
    columns being collapsed are exactly `natural_key(view) - per`, so those are
    what "latest" must mean, newest first, with knowledge_date last as a
    tiebreak. For derived_capm_beta that is as_of_date DESC; for
    derived_promoter_holding_change, whose key ends in quarter_end instead, it
    is quarter_end DESC. No call site has to think about it.

    Ordering by knowledge_date FIRST -- which is what the arg_max form this
    replaced effectively did -- is wrong as soon as anything is backfilled: a
    row computed for 2026-06-30 but written today has the newest
    knowledge_date in the table, so "current beta" would silently become a
    three-month-old beta. That has not bitten yet only because this warehouse
    has no backfilled derived_* vintages.

    Pass `order_by` explicitly to override, e.g. to rank by a value column.
    """
    if isinstance(per, str):
        per = [per]
    keys = natural_key(view)
    unknown = [c for c in per if c not in keys]
    if unknown:
        raise UnregisteredView(
            f"{view!r}: per={per} contains {unknown}, which is not in its declared "
            f"natural key {keys}. Grouping by a column outside the key is not a "
            f"well-defined collapse -- either the key is wrong or the caller means "
            f"a plain GROUP BY, not a vintage collapse."
        )
    if order_by is None:
        residual = [c for c in keys if c not in per]
        order_by = ", ".join([f'"{c}" DESC' for c in residual] + ["knowledge_date DESC"])

    inner = asof_sql(view, as_of, where=where)
    partition = ", ".join(f'"{c}"' for c in per)
    return (f"SELECT {columns} FROM (\n"
            f"    SELECT * FROM ({inner})\n"
            f"    QUALIFY row_number() OVER (PARTITION BY {partition} "
            f"ORDER BY {order_by}) = 1\n"
            f")")


def registered_views() -> list[str]:
    """Views that can be read point-in-time: declared key AND data on disk."""
    paths = view_paths()
    return sorted(v for v in DATASET_KEYS if v in paths)
