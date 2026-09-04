"""
Presentation and service layer over the warehouse.

Nothing in this package fetches, parses or writes -- `connectors/` owns the
first two and `core.storage.write_table()` owns the third. This package only
ever READS what is already archived and knowledge_date-stamped, and shapes it
for a human-facing surface.

`serializers/` exists so that the shaping happens exactly once, in Python,
regardless of which surface asks for it. The Streamlit dashboard is the only
consumer today; the React terminal will be the second. Domain knowledge like
"which XBRL concept corresponds to which screener.in line item" must never be
reimplemented per surface -- that is how two views of the same company start
disagreeing about its revenue.
"""
