"""
FLIGHTS -- OpenSky Network ADS-B state vectors.

WHY THIS IS HERE, AND THE HONEST CASE AGAINST IT
  Corporate-jet tracking is a real and long-standing buy-side technique: an
  acquirer's aircraft repeatedly on the ground at a target's home airport
  ahead of an announcement. It is legal (ADS-B is an unencrypted broadcast
  required by regulation) and it is public. It was flagged in PHASE7.md for
  an explicit decision rather than quietly included, and the decision was to
  build it.

  What it is NOT is a solved signal, and this module should not pretend
  otherwise:

  * Coverage is volunteer receivers, so it is dense over Europe and North
    America and much thinner over India and the Gulf. Absence of an aircraft
    is NOT absence of a flight.
  * A state vector carries icao24 and a callsign. Mapping a hex address to a
    beneficial owner is the actual work, and this module does not do it --
    there is no primary source for Indian beneficial ownership of aircraft
    that we have verified.
  * The anonymous/keyed API returns positions NOW, not history. Detecting a
    pattern requires our own archive to accumulate first. There is no
    backfill: what we did not collect, we do not have.

  So the deliverable here is a clean, archived positional record over
  airports we care about. Any inference on top of it is a later phase and a
  separate conversation.

AUTH -- verified live 2026-09-02
  OpenSky migrated to OAuth2 client-credentials. A client id/secret pair from
  the account page is exchanged at the Keycloak token endpoint for a bearer
  token with a 1800-second lifetime. The old username/password basic-auth
  flow is retired.

  Credentials are read case-insensitively so the .env spelling actually in
  use (`Opensky_clientId` / `Opensky_clientSecret`) works alongside the
  repo-conventional `OPENSKY_CLIENT_ID` / `OPENSKY_CLIENT_SECRET`. This is
  not cosmetic: os.environ is case-insensitive on Windows and case-SENSITIVE
  on Linux, so a plain get() on one spelling would work on the dev laptop and
  fail on any server this ever moves to.

  Rate limit is credit-based (4,000/day observed on this account, reported in
  the X-Rate-Limit-Remaining response header, which is logged into the raw
  sidecar so budget exhaustion is visible in the archive rather than as a
  mystery failure).
"""
from __future__ import annotations

import json
import os
import time

import pandas as pd

from core.config import load_env
from core.http import Fetcher
from core.storage import save_raw, write_table

TOKEN_URL = ("https://auth.opensky-network.org/auth/realms/opensky-network/"
             "protocol/openid-connect/token")
STATES_URL = "https://opensky-network.org/api/states/all"

# OpenSky's states/all vector layout, in order. Position 17 is documented but
# absent on some responses, so the parser tolerates a short row.
STATE_FIELDS = [
    "icao24", "callsign", "origin_country", "time_position", "last_contact",
    "longitude", "latitude", "baro_altitude", "on_ground", "velocity",
    "true_track", "vertical_rate", "sensors", "geo_altitude", "squawk",
    "spi", "position_source", "category",
]

# Bounding boxes worth watching for this book. (lamin, lomin, lamax, lomax)
BOXES = {
    # Jamnagar: RELIANCE's refinery complex, the world's largest, plus the
    # Sikka/Vadinar crude terminals. The single most position-relevant box.
    "jamnagar": (21.5, 69.0, 23.2, 70.6),
    # Mumbai: BSE/NSE, most large-cap head offices, the busiest business
    # aviation field in India.
    "mumbai": (18.5, 72.4, 19.6, 73.4),
    "delhi_ncr": (28.2, 76.7, 28.9, 77.6),
    # The Gulf approach -- Hormuz and the UAE/Oman terminals.
    "hormuz": (24.0, 54.0, 27.5, 58.5),
}

_TOKEN: dict = {"value": None, "expires_at": 0.0}


def _credential(*names: str) -> str:
    """
    Read a credential under any of several spellings, case-insensitively.

    See the module docstring: the .env in use spells these `Opensky_clientId`
    and `Opensky_clientSecret`, which resolves on Windows and breaks on Linux
    if looked up literally.
    """
    load_env()
    lowered = {k.lower(): v for k, v in os.environ.items()}
    for name in names:
        val = lowered.get(name.lower())
        if val:
            return val
    raise RuntimeError(
        f"Required OpenSky credential not set. Tried {list(names)}. "
        f"Add OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET to .env "
        f"(see .env.example)."
    )


def _access_token(force: bool = False) -> str:
    """
    Cached OAuth2 bearer token. Refreshed 60s before expiry.

    Cached deliberately: the token endpoint is not rate-limit-free, and a
    module that re-authenticates per request burns the daily credit budget on
    authentication instead of data.
    """
    now = time.time()
    if not force and _TOKEN["value"] and now < _TOKEN["expires_at"]:
        return _TOKEN["value"]

    client_id = _credential("OPENSKY_CLIENT_ID", "Opensky_clientId")
    client_secret = _credential("OPENSKY_CLIENT_SECRET", "Opensky_clientSecret")

    r = Fetcher(min_delay=0.0).post(TOKEN_URL, data={
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    })
    payload = r.json()
    if "access_token" not in payload:
        raise RuntimeError(
            "OpenSky token endpoint returned no access_token. The client "
            "credentials may be wrong or revoked."
        )
    _TOKEN["value"] = payload["access_token"]
    _TOKEN["expires_at"] = now + float(payload.get("expires_in", 1800)) - 60
    return _TOKEN["value"]


def opensky_states(box: str = "jamnagar", persist: bool = True) -> pd.DataFrame:
    """
    Current ADS-B state vectors inside one named bounding box.

    `box` is a key of BOXES -- jamnagar, mumbai, delhi_ncr or hormuz.

    WHY IT MATTERS: an archived positional record over the airports and
    terminals attached to this book's largest positions. Read the module
    docstring for what this does and does not support: coverage over India is
    volunteer-dependent, ownership mapping is not implemented, and there is
    no history before the day we start collecting.

    Returns one row per aircraft currently observed in the box. An empty box
    is a legitimate observation (night, no coverage, no traffic), so unlike
    most connectors here it is NOT an error -- but a missing `states` key is,
    because that means the response shape changed.
    """
    if box not in BOXES:
        raise ValueError(f"Unknown box {box!r}; known: {sorted(BOXES)}")
    lamin, lomin, lamax, lomax = BOXES[box]

    token = _access_token()
    fetcher = Fetcher(base_headers={"Authorization": f"Bearer {token}"}, min_delay=1.0)
    r = fetcher.get(STATES_URL, params={
        "lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax,
    })
    payload = r.json()

    remaining = r.headers.get("X-Rate-Limit-Remaining")
    save_raw("opensky", f"states_{box}", r.content, "json",
             {"url": STATES_URL, "box": box, "bbox": [lamin, lomin, lamax, lomax],
              "rate_limit_remaining": remaining})

    if "states" not in payload:
        raise RuntimeError(
            f"OpenSky response for {box} has no 'states' key -- got "
            f"{sorted(payload)[:8]}. The API shape changed."
        )

    states = payload.get("states") or []
    rows = []
    for s in states:
        # Pad short rows rather than zip-truncating: a missing trailing field
        # would otherwise silently shift every column after it.
        padded = list(s) + [None] * (len(STATE_FIELDS) - len(s))
        row = dict(zip(STATE_FIELDS, padded[: len(STATE_FIELDS)]))
        row["callsign"] = (row["callsign"] or "").strip() or None
        rows.append(row)

    out = pd.DataFrame(rows, columns=STATE_FIELDS)
    out["snapshot_time"] = pd.to_datetime(payload.get("time"), unit="s",
                                          errors="coerce", utc=True).isoformat() \
        if payload.get("time") else None
    out["box"] = box
    for col in ("longitude", "latitude", "baro_altitude", "geo_altitude",
                "velocity", "true_track", "vertical_rate"):
        out[col] = pd.to_numeric(out[col], errors="coerce")

    if persist and not out.empty:
        write_table(out, "opensky", f"states_{box}")
    return out
