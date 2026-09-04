"""
Quant Signal Engine (India Equities + F&O) -- Phase 7.

Every module here reads already-archived, already-knowledge_date-stamped
parquet through core.storage (no save_raw() calls -- there is no external
fetch happening in this package, only computation on data connectors/
already collected). Every module still ends by calling
write_table(df, "derived", "<dataset>"), so the derived SIGNAL ITSELF
becomes a fresh point-in-time vintage -- "what did our beta estimate for
RELIANCE look like as computed on 2026-08-24" stays a real, answerable,
backtestable question.

digest.py is the one exception with an external dependency: it calls
DeepSeek (core.llm) to phrase a terse label for an already-computed,
100%-deterministic ranking. No other module in this package touches an LLM.
"""
