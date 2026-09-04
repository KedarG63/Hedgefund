"""
Surface-agnostic shaping of warehouse rows into the tables a reader expects.

A serializer takes already-queried DataFrames and returns a DataFrame ready to
render. It does not query, and it does not know whether the caller is
Streamlit, FastAPI or a notebook.
"""
