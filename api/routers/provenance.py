"""
The provenance drill: every number reachable back to the bytes it came from.

This is the answer to "nothing is clickable" and the reason rule 1 exists. A
reader who distrusts a figure can follow it to the exact response a server
returned, with the timestamp and the sha256, without leaving the terminal.
"""
from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from api.responses import frame
from core import provenance as prov

router = APIRouter(prefix="/api/provenance", tags=["provenance"])


@router.get("/{view}")
def view_provenance(view: str):
    """
    The chain behind one view: upstream views, parsed vintages, archived bytes.

    JSON rather than Arrow -- this is a nested tree, not a table, and forcing
    it into columns would lose the shape that makes it readable.
    """
    from core.quality import DATASET_KEYS

    tree = prov.trace(view)
    has_raw = bool(tree["raw"]) or any(_any_raw(u) for u in tree["upstream"])
    return {
        "view": view,
        "natural_key": DATASET_KEYS.get(view),
        "trace": tree,
        "reaches_raw_bytes": has_raw,
        # Said plainly rather than implied by an empty list: "we have not
        # established the link" and "there are no bytes" are different claims.
        "note": None if has_raw else prov.UNRESOLVED_HINT,
    }


def _any_raw(node: dict) -> bool:
    return bool(node.get("raw")) or any(_any_raw(u) for u in node.get("upstream", []))


@router.get("/{view}/files")
def view_raw_files(view: str, limit: int = Query(25, le=200), fmt: str = "arrow"):
    """Flat list of archived files behind a view -- the table form of the tree."""
    rows = prov.raw_files(view, limit=limit)
    if not rows:
        return frame(pd.DataFrame(columns=["raw_dataset", "path", "bytes",
                                           "fetched_at_utc", "sha256"]), fmt)
    df = pd.DataFrame(rows).drop(columns=["meta"], errors="ignore")
    return frame(df, fmt, {"view": view})


@router.get("/raw/preview")
def raw_preview(path: str = Query(..., description="Path relative to data/raw/"),
                max_bytes: int = Query(4096, le=65536)):
    """
    The end of the drill: the archived bytes themselves.

    Bounded on purpose. The archive holds a 1.4 GB SEC bulk file, and the point
    is to SEE what arrived, not to move it through a browser.
    """
    try:
        return prov.read_raw(path, max_bytes=max_bytes)
    except FileNotFoundError:
        raise HTTPException(404, f"No archived file at {path!r}.") from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
