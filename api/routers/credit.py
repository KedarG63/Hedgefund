"""
Credit-rating agency feeds, unioned across CRISIL / ICRA / CARE.

Why this is a panel and not a footnote: rating actions cover PRIVATE issuers as
well as listed ones, which is the one routine window this warehouse has into
balance sheets that never file with an exchange -- exactly where a listed
parent's consolidated accounts can hide something.
"""
from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Query

from api.deps import AsOf, cursor, views
from api.responses import frame

router = APIRouter(prefix="/api/credit", tags=["credit"])

# Each agency publishes its own stable per-action id and its own column names.
# Normalising here rather than in the panel keeps the shape one thing.
AGENCIES = [
    ("CRISIL", "crisil_rating_actions",
     "pr_id AS action_id, company_name, rating_date, heading, action_type, "
     "industry_name AS category, document_url"),
    ("ICRA", "icra_rating_actions",
     "rationale_id AS action_id, company_name, rating_date, heading, action_type, "
     "rating_category AS category, document_url"),
    ("CARE", "care_rating_actions",
     "file_url AS action_id, company_name, published_date AS rating_date, "
     "file_title AS heading, CAST(NULL AS VARCHAR) AS action_type, "
     "file_type AS category, document_url"),
]


@router.get("/actions")
def actions(q: str | None = Query(None, description="Case-insensitive company substring."),
            as_of: AsOf = None, limit: int = Query(300, le=5000), fmt: str = "arrow"):
    """
    Recent rating actions across all three agencies, newest first.

    Rows are tagged `listed` where the company name matches an NSE/BSE listing.
    That split is the point of the panel: a downgrade at an unlisted subsidiary
    is a different signal from one at a listed parent, and the two arrive in
    the same undifferentiated feed.
    """
    from core.asof import asof_sql

    parts = []
    for agency, view, cols in AGENCIES:
        if view not in views():
            continue
        inner = asof_sql(view, as_of, columns=cols)
        where = ""
        if q:
            safe = q.replace("'", "''")
            where = f" WHERE lower(company_name) LIKE lower('%{safe}%')"
        parts.append(f"SELECT '{agency}' AS agency, * FROM ({inner}){where}")

    if not parts:
        return frame(pd.DataFrame(columns=["agency", "action_id", "company_name",
                                           "rating_date", "heading", "action_type",
                                           "category", "document_url"]), fmt)

    sql = (f"SELECT * FROM ({' UNION ALL '.join(parts)}) "
           f"ORDER BY rating_date DESC LIMIT {int(limit)}")
    return frame(cursor().execute(sql), fmt)
