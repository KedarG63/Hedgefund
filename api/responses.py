"""
Wire format: Arrow IPC by default, JSON on request.

WHY ARROW. The stack is already pandas + pyarrow + DuckDB, so a result set
reaches the wire without a serialisation step: DuckDB hands back an Arrow
table, pyarrow writes IPC bytes, and apache-arrow in the browser feeds AG Grid
and uPlot with no parse pass. It also sidesteps JSON's float round-tripping,
which matters when the payload is prices and z-scores rather than form fields.

JSON stays one query parameter away (?fmt=json) and is not a second-class
path -- it is what makes the API debuggable with curl, and what the contract
tests assert against. NaN/NaT are emitted as null rather than JSON's
non-standard NaN literal, which json.loads accepts but browsers do not.
"""
from __future__ import annotations

import io

import pandas as pd
from fastapi import Response

ARROW_MEDIA_TYPE = "application/vnd.apache.arrow.stream"


def _to_arrow_ipc(table) -> bytes:
    import pyarrow as pa

    sink = io.BytesIO()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue()


def frame(result, fmt: str = "arrow", meta: dict | None = None) -> Response:
    """
    Render a DuckDB result (or DataFrame) as the requested wire format.

    `meta` rides in headers rather than wrapping the body, so the Arrow and
    JSON paths carry the same information and the body stays a plain table in
    both. Header values must be latin-1, so keep meta to dates and counts.
    """
    headers = {f"x-qd-{k}": str(v) for k, v in (meta or {}).items()}

    if fmt == "json":
        df = result if isinstance(result, pd.DataFrame) else result.df()
        # to_json emits bare NaN, which is invalid JSON; go via object + where.
        payload = df.astype(object).where(pd.notna(df), None).to_dict(orient="records")
        return Response(
            content=_json_bytes({"rows": payload, "count": len(df), **(meta or {})}),
            media_type="application/json",
            headers=headers,
        )

    return Response(content=_to_arrow_ipc(_as_table(result)),
                    media_type=ARROW_MEDIA_TYPE, headers=headers)


def _as_table(result):
    """
    A pyarrow.Table from whatever the caller handed us.

    DuckDB's Arrow accessors are not consistent across versions -- .arrow()
    returns a RecordBatchReader here, a Table on older builds -- and a reader
    is single-use, so this must materialise rather than pass it through.
    """
    import pyarrow as pa

    if isinstance(result, pd.DataFrame):
        return _pa_from_pandas(result)
    if isinstance(result, pa.Table):
        return result
    if isinstance(result, pa.RecordBatchReader):
        return result.read_all()
    # to_arrow_table() is the current spelling; fetch_arrow_table() is
    # deprecated but is all older duckdb builds have.
    for name in ("to_arrow_table", "fetch_arrow_table"):
        fn = getattr(result, name, None)
        if fn is not None:
            return fn()
    obj = result.arrow()
    return obj.read_all() if isinstance(obj, pa.RecordBatchReader) else obj


def _pa_from_pandas(df: pd.DataFrame):
    import pyarrow as pa

    return pa.Table.from_pandas(df, preserve_index=False)


def _json_bytes(obj) -> bytes:
    import json

    def default(o):
        # Dates, Timestamps, numpy scalars and numpy ARRAYS -- anything pandas
        # hands back that json does not know. tolist() comes before item()
        # because a list column (derived_digest.contributing_signals is one)
        # arrives as an ndarray, and ndarray.item() raises for size != 1.
        if hasattr(o, "isoformat"):
            return o.isoformat()
        if hasattr(o, "tolist"):
            return o.tolist()
        if hasattr(o, "item"):
            return o.item()
        # str() is deliberate for the rest: these are display values, and a
        # lossy float coercion would be worse than a string.
        return str(o)

    return json.dumps(obj, default=default, allow_nan=False).encode()
