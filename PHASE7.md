# Phase 7 — OSINT / geospatial layer

Planned and built 2026-09-02. Every endpoint table below records what was
actually hit from this machine and what came back — per CLAUDE.md's rule
against inventing endpoints, nothing here is guessed, and the things that
could NOT be verified are listed as such rather than quietly included.

**Status: four connectors and two analytics modules shipped, 67 tests
passing, all six wired into `run_daily.py` and the dashboard.** Two items are
deliberately NOT built: IMD (blocked on pending API approval) and EIA (route
unverifiable without a key — see "Not built, and why" below).

## What shipped

| Module | Functions | Verified live | Warehouse rows |
|---|---|---|---|
| `connectors/chokepoints.py` | `portwatch_chokepoints()` | yes | 37,464 (2023-01-01 → 2026-08-30, 28 chokepoints) |
| `connectors/climate.py` | `noaa_oni()`, `cpc_india_rainfall()`, `cpc_india_rainfall_range()` | yes | 918 ONI + 90 monsoon days × 5 regions |
| `connectors/imd.py` | `imd_gridded_rainfall()`, `..._range()`, `available_years()` | yes | IMD's own 0.25° grid, 1990–2025 available; 1991–2020 backfilled as the climatology |
| `connectors/climate.py` | `firms_hotspots()` | **NO — needs key** | — |
| `connectors/geopolitics.py` | `gdelt_daily_events()`, `gdacs_alerts()` | yes | 362 alerts; GDELT 57,795 rows/day filtered |
| `connectors/flights.py` | `opensky_states()` | yes | 4 boxes, live ADS-B |
| `analytics/supply_chain.py` | `chokepoint_zscore()`, `reroute_balance()` | yes | 112 + 4 |
| `analytics/monsoon.py` | `enso_state()`, `monsoon_progress()` | yes | — |

`run_daily.py` gains eight jobs, registered BEFORE the cross-asset block so
its "must stay last" invariant holds. The dashboard gains two sub-tabs under
**Macro & Commodities** (Supply Chain, Monsoon & ENSO) rather than an eighth
top-level tab.

Tests: `tests/test_chokepoints.py`, `test_climate.py`, `test_geopolitics.py`,
`test_flights.py`, `test_analytics_supply_chain.py`,
`test_analytics_monsoon.py` — 67 passing, all offline.

### The analytics validate against both known crises

`reroute_balance()` was run against real backfilled history and separates the
two events correctly, which is the whole reason the module exists:

| Event | Corridor change | Substitute change | Absorbed | Reading |
|---|---|---|---|---|
| Red Sea 2024 (Bab el-Mandeb) | −36.0/day | +23.6/day | 65.5% | `rerouted` |
| Red Sea 2024 (Suez) | −27.4/day | +23.6/day | 86.2% | `rerouted` |
| Hormuz 2026 | −66.7/day | none absorbing | — | `no_substitute_route` |

Same tool, opposite readings. Rerouted cargo is a tonne-mile and freight-rate
story; destroyed cargo is a crude-availability story. (Panama 2024 reads
`traffic_destroyed`, which is also correct — that is the Gatún Lake drought.)

## The framing

The prompt for this phase was OSIRIS (`osirisai.live`), an open-source
situational-awareness platform: ~10,000 aircraft, ~2,000 satellites, ~1,400
CCTV feeds, earthquakes, wildfires, CVEs and 25 news RSS feeds rendered on a
GPU globe. The site itself returns 403 to automated fetches.

**We do not integrate the platform. We integrate its source list.**

Embedding someone else's live map breaks rule 1 outright: there are no raw
bytes to archive, no fetch timestamp, and no `knowledge_date` — you would be
looking at a render you cannot reproduce in 2028. The sources *underneath*
OSIRIS are USGS, NOAA, NASA, IMF, EIA, USDA and the UN. Those are primary
sources in exactly the sense the mission statement means: the agency itself,
no aggregator in the path. This phase is therefore *more* CLAUDE.md-compliant
than the screener.in exception, not a second erosion of the rule.

A map is not the deliverable either. The deliverable is a point-in-time
numeric series joined to instruments we already hold.

## Verified live, keyless (HTTP 200, real payload inspected)

| Source | Endpoint | What came back |
|---|---|---|
| IMF PortWatch | `services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0/query` | 28 chokepoints incl. Hormuz, Suez, Bab el-Mandeb, Malacca; daily vessel counts + deadweight capacity split by tanker/dry-bulk/container/roro/general-cargo |
| NOAA CPC ONI | `cpc.ncep.noaa.gov/data/indices/oni.ascii.txt` | 23 KB fixed-width text, DJF 1950 → present |
| GDELT 2.0 | `data.gdeltproject.org/gdeltv2/lastupdate.txt` | manifest of 15-min CSV zips (export / mentions / GKG) |
| OpenSky | `opensky-network.org/api/states/all?lamin=..&lomin=..` | live ADS-B state vectors; bbox over Mumbai returned real Air India / Air India Express traffic |
| UN Comtrade | `comtradeapi.un.org/public/v1/preview/C/A/HS` | India (reporter 699) annual imports, 500-row preview cap |
| NOAA CPC precip | `ftp.cpc.ncep.noaa.gov/precip/CPC_UNI_PRCP/GAUGE_GLB/RT/` | daily global gauge-analysis directory index |
| NVD | `services.nvd.nist.gov/rest/json/cves/2.0` | 385,313 CVEs, paged |
| USGS | `earthquake.usgs.gov/earthquakes/feed/v1.0/summary/*.geojson` | 200 |
| NASA EONET v3 | `eonet.gsfc.nasa.gov/api/v3/events` | 200 |
| GDACS | `gdacs.org/xml/rss.xml` | 200, 1.2 MB |
| NOAA SWPC | `services.swpc.noaa.gov/products/noaa-scales.json` | 200 |

Note: GDELT's `lastupdate.txt` 301-redirects. `Fetcher` already follows
redirects (`follow_redirects=True`), so this is a non-issue for us, but a bare
client that does not will see an empty body and read it as "no data".

## Endpoint shape confirmed, free API key required

| Source | Response without key | Meaning |
|---|---|---|
| NASA FIRMS | `400 Invalid MAP_KEY.` | path is right, key is a free self-service registration |
| USDA FAS PSD | `403 {"code":"API_KEY_MISSING"}` | path is right, key from `api.fas.usda.gov` |
| EIA v2 | empty response | documented API, but **not verified from this machine** — treat as unverified until a keyed call returns |

Keys go in `.env` (`FIRMS_MAP_KEY`, `USDA_FAS_KEY`, `EIA_API_KEY`) and
`.env.example`, loaded through `core.config` like every other credential.

## NOT verified — SPA shells, needs a `discover_*` tool, do not guess

| Target | What we got | Next step |
|---|---|---|
| IMD rainfall (`mausam.imd.gov.in/responsive/rainfallinformation.php`) | 253 KB HTML shell. Page source references `api.imd.gov.in` but no service path | JS-bundle read, same recipe as `tools/discover_rbi_services.py` |
| PPAC petroleum consumption (`ppac.gov.in/consumption/`) | 38 KB HTML | same |
| PortWatch **ports** layer (`PortWatch_ports_database`) | **429 on every attempt — never once succeeded** | retry off-peak before claiming it works |
| PortWatch bulk CSV | a guessed ArcGIS Hub item id returned `"Item does not exist or is inaccessible"`; the datasets page is an SPA with no ids in its HTML | JS-bundle read to recover real item ids |

The IMD one matters most and is the one we have least of. India monsoon
rainfall departure is the single highest-value India macro series on this
list, and we currently have no verified path to it. **This is the first thing
to solve, and it is a discovery task, not a coding task.**

## Findings the live probing produced

### The Hormuz series is CORRECT — it is the 2026 crisis, and it validates the feed

An earlier draft of this document called the Hormuz series suspect and
proposed building around it. **That was wrong, and how it was wrong is the
most useful thing in this document.**

The reasoning ran: Hormuz reads 1-9 vessels/day for the whole trailing
fortnight, an order of magnitude below any plausible count for the world's
most important oil chokepoint, while Suez on the same layer and the same dates
reads a sane 33-55/day — therefore the layer is not uniformly trustworthy per
chokepoint. Every measured number in that sentence was accurate. The
conclusion did not follow from them.

Pulling the full monthly history settles it:

```
  Strait of Hormuz -- avg vessels/day
  2019-01    55.6        2025-06   102.2        2026-03     3.2   <-- break
  2021-06    91.4        2025-12    55.5        2026-04     7.1
  2022-08   103.3        2026-01    58.5        2026-05     3.9
  2023-08   104.4        2026-02    78.3        2026-06    12.9
  2024-05   105.1                               2026-07    10.2
                                                2026-08     4.9
```

Hormuz runs 55-105 vessels/day continuously for seven years — 2019-01 through
2026-02 — then breaks to 3.2/day in 2026-03 and never recovers. A degraded
feed does not produce seven clean years and then a cliff.

**The cliff is an event.** The US/Israel air war against Iran opened
2026-02-28. Independent reporting puts roughly 6 ships/day through the strait
in the first week of March against ~100/day in February; this dataset says 3.2
against 78.3. The partial reopening under the June US-Iran MOU appears as the
rise to 12.9, and the collapse of that agreement in early July appears as the
fall back through 10.2 to 4.9. The series tracks the reported chronology month
by month. It is not broken. It is the single most important supply-chain fact
of 2026, correctly recorded.

**Suez was never a clean baseline either.** It reads 71-77 vessels/day through
all of 2023, then drops to 39-48 from 2024-01 and stays there. That is the
Houthi Red Sea campaign. The "sane, stable" Suez level used as the reference
point was itself a post-crisis level — a shocked series mistaken for a normal
one.

### The mass balance is what actually proves the dataset

The decisive test is not whether a level looks plausible. It is whether the
vessels that leave one chokepoint arrive at the alternative route. They do:

| Route | 2023-11 | 2024-06 | Change |
|---|---|---|---|
| Bab el-Mandeb | 77.3/day | 31.4/day | **-45.9** |
| Suez Canal | 76.6/day | 39.0/day | -37.6 |
| Cape of Good Hope | 49.3/day | 95.5/day | **+46.2** |

Bab el-Mandeb loses ~46 vessels/day and the Cape of Good Hope gains ~46 in the
same months. Ships did not disappear — they sailed around Africa, which is
what happened physically and what a correct AIS attribution must show. Two
independent crises (Red Sea 2024, Hormuz 2026), both captured, with traffic
conserved across the substitute route.

**This dataset is trustworthy, and now demonstrably so.**

### The methodological lesson — the part to keep

The error was judging a *level* against a remembered prior instead of checking
the series' own history. The prior was stale: this analysis was drafted by a
model whose knowledge ends before the 2026-02-28 escalation, so "implausible"
meant only "unfamiliar to me" — and it was applied confidently enough to
nearly discard the most valuable series in the feed. The user's challenge, not
the analysis, is what caught it.

The rule this phase should adopt for every geospatial series:

1. **Never validate a level against a remembered prior.** Pull the history and
   look for a structural break instead. A break has a date and is checkable; a
   level is only an opinion.
2. **Cross-validate against the substitute route.** If a corridor collapses,
   its alternative must absorb the traffic. A conserved mass balance is nearly
   impossible for a corrupted feed to fake, and costs one extra query.
3. **Date the break first, then look for the event** — not the reverse. The
   break date came first here; the reporting confirmed it afterwards.

This cuts the opposite way to Phase 4's schema-drift guard. That catches a
feed changing under us. This catches *us* being wrong about a feed that is
fine — the more expensive error, because it discards real signal silently
while looking like diligence.

### PortWatch sits on a shared, heavily throttled ArcGIS tenant

Repeated `429 — API calls quota exceeded (6003 request units)! maximum
allowed request units (6000) per Minute.` The quota is **shared across every
consumer of that ArcGIS organisation**, not per-caller, so it fails
unpredictably regardless of our own politeness. Four consecutive retries with
30 s spacing all 429'd at one point.

`Fetcher` already treats 429 as retryable, so this is survivable — but it
dictates the integration shape: **one archived pull per day, never ad-hoc
querying from the dashboard.** A dashboard panel that queries PortWatch live
will show empty panels at random and look exactly like a broken connector.

### PortWatch lag varies, 3-9 days

Latest available was 2026-08-23 against a 2026-09-01 run, then 2026-08-30
against a 2026-09-02 run. So the lag is not a fixed 9 days — it moves. The
`run_daily` job therefore asks for a 30-day trailing window rather than a
7-day one, which would sometimes fetch nothing at all and look like a
healthy no-op. Either way this is a structural-trend input, not a trading
signal, and every dashboard panel states its own as-of date.

## Findings from the build

Three real bugs, all caught by testing rather than by reading output. Each is
now regression-tested.

### GDELT uses TWO country-code systems in one file — 40% of the universe was silently dropped

`Actor1CountryCode` / `Actor2CountryCode` are CAMEO 3-letter codes (`IRN`,
`USA`). `ActionGeo_CountryCode` is FIPS 10-4 **2-letter** (`IR`, `US`).
Filtering all three fields against one 3-letter set makes the geography term
dead code — it can never match.

The live smoke test did not reveal this. It returned 41,153 perfectly
reasonable rows, because the two actor fields matched plenty on their own.
Only a unit test with a geography-only row exposed it. After the fix the same
day returns **57,795 rows — the bug was discarding 16,642 rows/day, 40.4% of
the universe.**

And FIPS is not ISO. The ones that actually bite: Iraq `IZ`, Kuwait `KU`,
Oman `MU`, Yemen `YM`, Russia `RS`, China `CH`.

### `Fetcher`'s default retry budget cannot outlast PortWatch's quota window

The first full backfill died with `GET failed after 4 tries`. Not bad luck:
the default 4 retries back off 1+2+4+8s ≈ 15s, while the ArcGIS quota resets
per **minute**. The budget was structurally incapable of surviving a quota
window. Raised to 8 retries (1+2+…+128s) for this connector only, sized
against the documented window rather than picked by feel — the backfill then
completed, 37,464 rows in 110s.

Nothing was lost to the failed attempt, because the raw pages were already
archived before the parse. That is rule 1 paying for itself.

### CPC unit scaling: one helper, two variables, two different units

`rain` is in tenths of a mm/day; `gnum` is a plain station count. An early
version baked the ÷10 into the shared area-mean helper, so it silently
corrupted the station count — and separately unpacked the `(mean, n_cells)`
tuple backwards, reporting "7280 stations per cell" for a region that merely
had 728 cells. The helper now returns raw grid units and the caller applies
the scale factor, which is the only arrangement that cannot be wrong for one
of the two.

## What was built

Four connectors on the existing five-point contract, two analytics modules,
and sub-tabs rather than a new top-level tab. See "What shipped" at the top
for the row counts.

### Connectors

| Module | Functions | Persist | Schedule |
|---|---|---|---|
| `connectors/chokepoints.py` | `portwatch_chokepoints()` | yes | daily, 30-day trailing window |
| `connectors/climate.py` | `noaa_oni()` | yes | `monthly` |
| `connectors/climate.py` | `cpc_india_rainfall()`, `..._range()` | yes | daily, 3 days back |
| `connectors/climate.py` | `firms_hotspots()` | — | **not scheduled — needs key** |
| `connectors/geopolitics.py` | `gdelt_daily_events()` | yes | daily, 3 days back |
| `connectors/geopolitics.py` | `gdacs_alerts()` | yes | daily |
| `connectors/flights.py` | `opensky_states()` | yes | daily, 4 boxes |

GDELT publishes every 15 minutes. That is far too hot for a laptop that
sleeps — the same reason Phase 6 deferred cron. We pull one daily 1.0 file
(~6 MB, one request instead of 96 — rule 4) and **filter to a universe before
keeping anything** (rule 5). The raw zip is archived; the parsed table is the
filtered slice.

### Analytics

- `analytics/supply_chain.py` — `chokepoint_zscore()` (transit z-scores vs a
  NON-overlapping trailing baseline, per vessel class) and `reroute_balance()`
  (the mass balance that separates a routing event from a supply event).

  Note what the z-score does **not** say. Its baseline is 90 days, so by
  September 2026 the Hormuz closure is *inside* the baseline and the corridor
  scores only −4.6/day. That is correct behaviour — it measures deviation
  from the current regime, not from the pre-crisis world. To see the break
  itself, pass an `as_of` before it. Both readings are useful; conflating
  them is not.

- `analytics/monsoon.py` — `enso_state()` (phase, trajectory, base rate) and
  `monsoon_progress()` (season-to-date cumulative by region, with coverage).

  These are deliberately NOT joined into one number. ENSO is a prior available
  months ahead; rainfall is the observation that updates it. A single blended
  score would hide which half is doing the work.

  Current readout: ONI **+1.39 (MJJ 2026), El Niño, strengthening** (+1.78
  over six seasons) in the window that leads the monsoon — with 91 of 91
  season days observed.

  No departure-from-normal is computed anywhere, on purpose. CPC's normals
  are not IMD's, and a "departure" quoted against the wrong baseline is worse
  than no departure at all.

### Dashboard

Folded into **Macro & Commodities** as sub-tabs alongside the existing
`sub_macro` / `sub_gold`. No eighth top-level tab — a thin dataset in its own
tab is how dashboards rot, and this phase starts thin. Charts use
`graph_objects`, matching every other chart in `app.py`; `plotly.express` is
not imported there.

### Why each source matters (contract rule 5)

| Source | Research thesis | Instruments |
|---|---|---|
| Chokepoint transits | India imports ~85% of its crude, much of it Gulf-origin, and **Hormuz has been effectively closed since 2026-02-28** — this is a live position-relevant series, not a hypothetical | RELIANCE (Jamnagar), IOC, BPCL, HPCL, SCI, GE Shipping |
| NOAA ONI | ENSO → monsoon → rural demand and food CPI → RBI policy, which we already collect | M&M, Escorts, Hero, Coromandel, Chambal, HUL, Dabur; ties to `rbi_*` datasets |
| EIA stocks | the US crude leg pairing with the CME/MCX settlements in `commodities.py` | crude complex — **NOT BUILT, see below** |
| FIRMS | Punjab/Haryana stubble burning; Indonesian/Malaysian palm fires → edible-oil import bill → food CPI | agri, FMCG input costs |
| GDELT | geopolitical event/tone stream; feeds `core/llm.py` and `analytics/digest.py` | macro overlay |
| GDACS/EONET | cyclone landfall on the east coast → port and refinery shutdown | ports, refiners, insurers |

Structurally the chokepoint work is the **same trick as the India gold
premium**: a series nobody sells, computable only because we own every leg.
That is the pattern worth repeating, not the map.

## IMD, obtained without approval

The API is not the only door, and it was the wrong door to be waiting at.

IMD Pune publishes its gridded rainfall product as **plain annual NetCDF
downloads behind an ordinary HTML form** — no key, no registration, no
approval. That is the authoritative dataset itself, not a derived copy or a
third-party mirror, so it satisfies the primary-source rule outright.

```
POST https://www.imdpune.gov.in/cmpg/Griddata/RF25.php
     body: RF25=<year>        (Referer required; stateless otherwise)
  -> Content-disposition: attachment; filename=RF25/ind<year>_rfp25.nc
  -> ~25 MB NetCDF-3 classic (magic CDF\x01)
```

0.25° daily rainfall over India: 135 lon (66.5–100.0E) × 129 lat (6.5–38.5N),
in **millimetres** (not CPC's tenths), `-999` missing. Read with
`scipy.io.netcdf_file` — NetCDF-3 classic needs no netCDF4/xarray, and scipy
was already in the stack for Black-Scholes.

**Coverage is 1990–2025, 36 years** — read off the form's own dropdown. An
earlier draft of `connectors/imd.py` claimed "1901–present" from general
knowledge; the dropdown is the authority and it starts at 1990. That still
covers the WMO-standard **1991–2020 normal period in full**, which is what the
climatology is built from.

### What this unlocks

PHASE7's open question #2 was "no departure-from-normal exists yet". It does
now, and on IMD's own basis rather than a borrowed one.

**And it cross-checks against a number nobody in this pipeline computed.**
The full 1991–2020 climatology (30 years, 54,790 rows, zero missing years):

| Region | Jun–Sep normal | sd |
|---|---|---|
| all_india | **856.6 mm** | 70.0 |
| central | 961.7 | 116.3 |
| east_northeast | 1372.6 | 152.6 |
| northwest | 571.9 | 88.4 |
| south_peninsula | 809.3 | 122.1 |

IMD's published all-India Long Period Average for the same season is roughly
868 mm. Landing **within ~1.3%** of the official LPA — via an independent
path, from raw grids, through our own cosine-weighted rectangle — is strong
evidence that the NetCDF parse, the millimetre units, the latitude
orientation and the area weighting are all correct *simultaneously*. Any one
of them being wrong would move this number a long way. (A partial 18-year
backfill gave 852.6 mm along the way, converging as years were added, which
is what a correct climatology should do.)

It is not an exact match and should not be: our `all_india` box is a
rectangle, while IMD's official figure is weighted over actual subdivision
boundaries. Close-but-not-identical is the right answer here; an exact match
would suggest we had accidentally re-derived their published aggregate rather
than measured the grid.

The regional ordering is independently sane too — east/northeast wettest at
1372.6 mm, northwest driest at 571.9 mm — which a flipped latitude axis
would have inverted.

The day-of-year truncation guard is visible in the same run: the same
climatology closed on 30 August reads 683.9 mm against 856.6 mm for the full
season. Comparing a season-to-date total against the latter is precisely the
fake deficit `imd_normals(upto_month=, upto_day=)` exists to prevent.

### The trap, and the honest reconciliation

IMD publishes **annual files in arrears** — the dropdown ends at 2025, and
posting `RF25=2026` returns HTTP 200 with a valid `Content-disposition` and a
**zero-byte body**. A naive caller records a success and no data; archiving
that would put a 0-byte file in the immutable archive that reads forever
after as "a year with no rain". `imd_gridded_rainfall()` raises on it.

So IMD supplies the *climatology* but cannot supply the *current season*. CPC
still does that — and a CPC observation against an IMD normal is two
different instruments (different gauge networks, 0.5° global vs 0.25°
India-only, different interpolation) read as one. That comparison is exactly
how a fake deficit gets manufactured.

The reconciliation, rather than the fudge:

- `imd_normals()` — IMD's Jun-1-to-date normal per region, **truncated to the
  same day-of-year as the observation**. Comparing season-to-date against a
  full-season normal is the single easiest way to invent a deficit.
- `cpc_imd_bias()` — measures `mean(CPC)/mean(IMD)` per region on years where
  both exist, over the identical window. Years where CPC's archived coverage
  is under 90% of IMD's days are dropped, because sparse coverage reads as a
  dry bias that is purely an artefact.
- `monsoon_departure()` — uses IMD directly when IMD covers the season;
  otherwise divides the CPC observation by that measured ratio. Every row
  carries a `basis` column saying which happened.

**If the bias cannot be measured, no departure is reported at all** — the
observation is returned with `basis="cpc_uncorrected"` and a null departure.
A departure whose basis is unstated is a departure you cannot act on.

Both grids are averaged over the *same* region boxes with the *same*
cosine-latitude weighting (`climate.area_weighted_mean`, now shared by both
readers), which is what makes the ratio meaningful in the first place.

## Not built, and why

**~~IMD — blocked~~ → SOLVED 2026-09-03 without the API.** See "IMD, obtained
without approval" below. The API approval is still pending and no longer
blocks anything that matters.

**EIA — route unverifiable without a key.** The v2 root returns a clean
`403 API_KEY_MISSING`, which proves the API and its auth shape. But the
petroleum-stocks route guessed from the docs returns an **empty body even
with a deliberately bogus key**, where a valid route with a bad key returns
the same error envelope the root does. That is evidence the route is wrong,
and the self-describing route index needs a key to enumerate.

So this would have been a guessed endpoint (rule 3) and untested scaffolding.
Phase 4 already found that two scaffolded-but-never-run connectors were both
broken; repeating that pattern knowingly is worse than leaving a gap. **Get a
free key at eia.gov/opendata/register.php, enumerate the real route from the
API itself, then build.** Half a day's work once the key exists.

**FIRMS — written but never run.** Unlike EIA, its route IS confirmed: a bad
key returns the specific error `Invalid MAP_KEY`, which only a real route
produces. So the connector is written and marked UNVERIFIED in its own
docstring, is not scheduled in `run_daily.py`, and raises loudly without
`FIRMS_MAP_KEY`. The CSV parse is written against documented columns, not an
observed response — check the first real call before trusting it.

## Deliberately rejected

- **CCTV feeds (~1,400 cameras)** — zero research value and genuine terms-of-use
  exposure for a fund. Not a close call.
- **Satellite position tracking (~2,000 objects)** and the **live flight globe** —
  spectacle. No series comes out of either.
- **NOAA SWPC space weather** — verified reachable, kept on the list only
  because it is nearly free; no thesis unless we trade power or satcom.
- **NVD CVE → ticker** — the feed is trivial, the mapping from a CVE to an
  issuer's revenue exposure is the entire difficulty. Noisy. Tier 3 at best.
- ~~**OpenSky corporate-jet tracking**~~ — **decided IN and built.** Was
  flagged here for an explicit call rather than quietly included; credentials
  were supplied and `connectors/flights.py` shipped. The caveats stand and
  are restated in its docstring rather than buried here: coverage over India
  is volunteer-dependent so absence of an aircraft is NOT absence of a flight;
  tail-number → beneficial-owner mapping is unimplemented and is the actual
  work; and there is no history before the day we start collecting. The
  deliverable today is a clean archived positional record over Jamnagar,
  Mumbai, Delhi NCR and Hormuz — inference on top of it is a later phase.

## Legal

Cleaner than anything currently in the pipeline. USGS, NOAA, NASA, EIA and
USDA are US-government works; IMF PortWatch and UN Comtrade are IGO open-data
products. None of them carry the redistribution restriction that NSE/BSE data
does, so this layer does not add to the licensing exposure already flagged for
exchange data.

One exception: **GDELT indexes third-party news, and the underlying article
text is copyrighted.** Use the event, actor, geography and tone metadata.
Do not archive or surface article bodies.

## Decisions — resolved

1. ~~Hormuz.~~ **Resolved: the series is correct** and became the lead reason
   to build the phase. `reroute_balance()` now classifies it
   `no_substitute_route`, distinct from the 2024 Red Sea `rerouted` case.
2. ~~IMD.~~ **Blocked, not declined** — approval pending. CPC stands in; see
   "Not built, and why".
3. ~~OpenSky.~~ **In.** Built, with its limits documented rather than
   buried.
4. ~~Scope for a first cut.~~ **Superseded** — the whole Tier 1/2 set landed
   except the two blocked items.

## Regime detection — the phase's own lesson, turned into code

Added 2026-09-03. The phase's central finding was *"never validate a level
against a remembered prior; pull the history and date the break."* That was a
paragraph in this document. It is now `regime_breaks()` and
`current_regime()` in `analytics/supply_chain.py`.

Binary segmentation on a Welch t-statistic over a deseasonalised series, with
a minimum segment length so an incident cannot masquerade as a regime. **No
event dates are hardcoded anywhere.** Run against the archive it produces:

| Chokepoint | Break dated | Change | t |
|---|---|---|---|
| Strait of Hormuz | **2026-03-02** | 89.5 → 7.7 (−91.4%) | 100.3 |
| Bab el-Mandeb | 2024-01-12 | 72.3 → 33.2 (−54.0%) | 83.3 |
| **Cape of Good Hope** | 2023-12-29 | 50.9 → **89.0 (+74.9%)** | 34.0 |
| Suez Canal | 2024-01-20 | 72.0 → 40.2 (−44.2%) | 53.5 |
| Kerch Strait | 2021-11-09 | 52.9 → 19.9 (−62.4%) | 43.8 |

It dates the Hormuz collapse to **two days after** the 2026-02-28 escalation,
from data alone. And it finds the 2024 Red Sea crisis at Bab el-Mandeb and
Suez *together with the mirror-image positive break at the Cape of Good Hope*
— the mass balance rediscovered automatically rather than asserted.

Deepening the archive to 2019 sharpened this materially: on the 2023-start
history the Hormuz break dated to 2026-03-04 at t=47.4; with pre-crisis years
present it moves to 2026-03-02 at t=100.3.

### Three numbers, because one is not enough

`chokepoint_zscore()`'s fixed 90-day baseline has the flaw documented above:
once a disruption is older than the baseline it sits *inside* it, and Hormuz
— the largest supply event in the archive — scores about −4.6/day.
`current_regime()` answers the questions separately:

| Column | Question | Hormuz today |
|---|---|---|
| `z_vs_regime` | unusual *for this regime*? | **+0.01** — entirely normal |
| `regime_vs_previous_pct` | how far did the regime move? | −39.9% |
| `vs_baseline_pct` | how far from the pre-break world? | **−93.1%** |

A route can be perfectly normal for a closed strait, and collapsing these
loses the story. The third column exists because Hormuz broke **twice** —
again inside its own closure, 7.7 → 4.6 vessels/day — so "vs previous regime"
reports −40% for a corridor down 93% on where it started. Quote the baseline
column when asking how disrupted a route is.

### The deseasonalising bug, and a limit that is not a bug

The obvious implementation — group by day-of-year, subtract the mean — is
wrong here and hides itself. Any day-of-year seen only **once** gets a
climatology equal to its own value, so its residual is exactly zero: under two
years of history the entire signal is annihilated and the detector silently
finds nothing. Even at four years it attenuates every real break by 1/n,
because the break's own year is inside the mean being subtracted from it —
roughly 25% off every effect size here, invisible in the output.

Replaced with a two-harmonic annual fit. A step is not in the span of a few
harmonics, so the cycle comes out and breaks stay in.

**The remaining limit is genuine, not fixable.** Under ~2 years a step and an
annual cycle are *confounded* — with 400 days and a step at day 200, low
days-of-year are almost all pre-step and high ones post-step, so a harmonic
absorbs much of the step. The information to separate them is not present. So
below `MIN_SEASONAL_YEARS` the series is left undeseasonalised and the minimum
segment length carries the load, with a higher risk of a seasonal false
positive accepted explicitly.

Also known: two harmonics fit a smooth cycle well and an **ice-bound route
badly**. The Bering Strait is a near-square wave — closed much of the year,
open in summer — and its breaks should not be read literally. Flagged in the
docstring and in the dashboard caption rather than left for someone to trip
over.

## Open questions for the next session

1. **Horizon on the Hormuz signal.** *Partly addressed* — `current_regime()`
   now separates "unusual for this regime" from "displaced from baseline",
   which is the measurement half of the question. The *position* half is
   still open: none of these three numbers is yet a rule that sizes a trade
   in RELIANCE, IOC/BPCL/HPCL or the shippers. The most likely residual
   information remains the deviations (the June MOU reopening at 12.9/day and
   its July collapse) and the re-routing series — Cape of Good Hope tanker
   traffic rose 13.5 → 23.0/day between 2026-02 and 2026-04.
2. ~~**No departure-from-normal exists yet.**~~ **Resolved 2026-09-03** — IMD's
   own gridded archive turned out to be downloadable without the pending API
   approval, so the climatology is IMD's rather than borrowed. See "IMD,
   obtained without approval". What remains open is narrower: the CPC→IMD
   bias ratio is measured on only three overlap years (2023–2025), which is
   thin. Widen it as CPC history accumulates, and retire the correction
   entirely once IMD publishes 2026.
3. ~~**Backfill depth.**~~ **Resolved 2026-09-03.** Chokepoints now hold
   **2019-01-01 → 2026-08-30**, 2,799 days, 78,372 rows. The connector gained
   an `until` bound so a deepening backfill fetches only the missing older
   window instead of re-pulling everything — which matters on a shared,
   throttled tenant. The extra years measurably sharpened break detection
   (Hormuz t=47.4 → t=100.3, and the dated break moved two days closer to the
   actual escalation).
4. **EIA and FIRMS** need keys; see "Not built, and why".
5. **GDELT is one archived day, not a series.** The connector and its
   universe filter are proven, but a conflict/tone *series* needs a backfill
   loop over daily files before `analytics/digest.py` can consume it.
