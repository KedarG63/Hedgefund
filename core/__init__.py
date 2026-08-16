"""
quantdata core package.

Importing `core` (or anything under it) loads .env into the process environment
FIRST. This matters: core.storage reads QUANTDATA_ROOT at import time, so the
env must already be populated by the time that module body executes.
"""
from core.config import load_env

load_env()
