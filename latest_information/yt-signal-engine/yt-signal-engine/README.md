# yt-signal-engine

Turns a list of YouTube channels into a signal dashboard: headline, summary,
takeaways, and a set of extracted signals per episode — each one carrying a
verbatim quote, a timestamp that deep-links into the video, a stance, and a
confidence score.

Built for the case where you trust a handful of channels but cannot spend four
hours a week watching them, and where the things worth researching keep getting
buried between the sponsor read and the sign-off.

```
python run.py run          # discover -> transcribe -> extract -> render
open data/dashboard.html
```

---

## How it works

```
RSS feed  ──▶  transcript  ──▶  chunk  ──▶  MAP extract  ──▶  REDUCE  ──▶  SQLite  ──▶  dashboard
(no API key)   (captions)      (~12k)      (per slice)       (dedupe,      (compounds)   (HTML)
                                                              rank, brief)
```

**1. Discover — `engine/feeds.py`**
Every YouTube channel publishes a public Atom feed at
`youtube.com/feeds/videos.xml?channel_id=UC…`. No API key, no quota, no OAuth.
It carries the last ~15 uploads with title, publish time, view count, and the
description — which for both of your channels includes the creator's own
chapter list. That chapter list is free structure and the engine parses it:
the creator is telling you exactly where the topics are, which is better than
anything you could infer from the transcript.

For deeper history than 15 videos, `list_channel_videos_deep()` uses yt-dlp
(optional dependency).

**2. Transcribe — `engine/transcripts.py`**
`youtube-transcript-api` reads the caption track YouTube already serves to the
player. No download, no audio, no key, ~2 seconds per video. Auto-generated
captions count, and both tracked channels have them on everything.

Two caveats worth knowing before you deploy this anywhere:

- **Run it on a residential IP.** YouTube rate-limits datacenter ranges hard.
  On your laptop this is fine. On a $5 VPS it will start failing, and you'll
  need a proxy (`YT_PROXY_HTTP` in `.env`).
- **A video with no captions at all** falls back to `whisper_fallback()`,
  which needs `yt-dlp` + `faster-whisper` and turns 2 seconds into minutes.
  It is off by default because you almost never need it.

**3. Extract — `engine/analyze.py`**
This is the part that determines whether the output is worth reading.

The transcript is cut into overlapping ~12k-character slices and each is read
on its own (MAP), then every slice's output is put back in front of the model
together to be merged, ranked and summarised (REDUCE).

The slicing is not an optimisation, it is the quality mechanism. A 30-minute
episode read in one pass produces generic summary mush, because the model
compresses. Read in slices, it stays specific and keeps its timestamps
straight.

Three rules in `prompts/extract.md` do most of the work:

- **Quote or it did not happen.** Every signal carries 10–40 words copied
  verbatim. If the model can't quote it, it drops it. This is the single
  biggest defence against confident-sounding invention.
- **Attribute, don't endorse.** "The hosts argue X because Y", never "X".
  These are opinions on a podcast, and the dashboard should never let you
  forget that.
- **`contested` is a first-class stance.** When the speakers disagree or argue
  both sides, that gets flagged rather than flattened into a summary. Those are
  usually the most valuable items on the board.

Output is enforced with tool schemas rather than "please return JSON", so
parsing is never the flaky part.

**4. Store — `engine/store.py`**
One SQLite file. The point of persisting rather than regenerating is that
signals compound: once six months of episodes are in there,
`python run.py search "memory"` answers "every time this channel talked about
memory pricing, what did they say and when" without a single LLM call.

**5. Render — `engine/dashboard.py`**
One self-contained HTML file, no build step, no CDN. Client-side filtering by
source, signal type and free text. `--fragment` emits body-only HTML for
publishing as a hosted page.

---

## Signal types

| Type | What it is | Rendered as |
|---|---|---|
| `investable_idea` | A specific claim about a company, ticker, sector or instrument — thesis, valuation, catalyst, named risk | **Idea** |
| `macro_structure` | How a market, financing structure, regulation or flow actually works or is changing — the mechanism, not the mood | **Macro** |
| `research_thread` | Interesting but unverified; worth an hour of your own digging. Always carries a `research_question` phrased so you can hand it straight to a research agent | **Dig** |
| `historical_precedent` | A specific past trade, blow-up or structural failure told with mechanism and outcome — a base rate for today. Always carries an `era` (e.g. "1998" or "2007-2009") | **Precedent** |

Turn one off by removing it from `analyze.signal_types` in `config.yaml`.

---

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env      # add ANTHROPIC_API_KEY
python run.py backfill --per 10
```

`config.yaml` holds the channel list. Each channel has a `lens` — a few lines
telling the model what the channel is *for*, and what to ignore. This matters
more than it looks: without it the extractor treats the TIKR sponsor read as
an investable idea. Adding a channel is four lines:

```yaml
  - handle: "@SomeChannel"
    channel_id: "UC..."          # find it in the page source, or the og:url meta tag
    name: "Some Channel"
    lens: >
      What this channel is for, and what to ignore.
```

## Commands

```bash
python run.py pull                # discover + fetch transcripts only
python run.py analyze --limit 3   # extract from whatever is pending
python run.py build --json        # re-render without re-analysing
python run.py run                 # all three — this is your cron target
python run.py backfill --per 10   # seed history, ignoring the lookback window
python run.py search "memory"     # query the knowledge base
python run.py status              # what's in the box, what failed and why
```

Running daily:

```cron
0 7 * * *  cd /path/to/yt-signal-engine && /usr/bin/python3 run.py run >> data/run.log 2>&1
```

## Cost

The dominant cost is the MAP pass: roughly one call per 12k characters, so a
30-minute episode is about 4–6 extraction calls plus one synthesis call. At
Sonnet pricing that is cents per episode. Two channels at four episodes a week
lands in the low single-digit dollars a month.

Levers if you want it cheaper: raise `chunk_chars` (fewer, longer calls —
quality drops noticeably past ~20k), raise `min_confidence` to discard more,
or drop a signal type.

---

## What's in the box

```
config.yaml            channels, thresholds, model, output paths
prompts/
  extract.md           the MAP prompt — edit this to change what counts as signal
  synthesize.md        the REDUCE prompt — edit this to change the briefing style
engine/
  feeds.py             Atom feed parsing, chapter extraction
  transcripts.py       caption fetch, timestamp anchoring, whisper fallback
  analyze.py           chunking, MAP/REDUCE, tool schemas
  store.py             SQLite schema and queries
  dashboard.py         HTML renderer (full page and fragment)
  cli.py               command line
seed_backfill.py       the first nine episodes, pre-extracted (see below)
data/
  signals.db           the knowledge base
  dashboard.html       the output
```

## About the seeded backfill

`seed_backfill.py` contains the extraction output for the first nine episodes,
produced by running the same prompt discipline by hand so the dashboard has
real content before you've spent a cent. Once you run the engine yourself it's
redundant — the pipeline writes the same rows.

One difference worth knowing: those nine transcripts were fetched out of band,
without per-line timestamps, so their signals are anchored to the creator's
published chapter boundaries rather than to the exact second. Three of the
Hamish Hodder videos publish no chapter list at all, so their signals carry no
timestamp link. When you run the pipeline yourself,
`youtube-transcript-api` returns per-line timings and every signal gets an
exact anchor.

## Known limits

- **15 videos per feed.** Deeper history needs yt-dlp or the YouTube Data API.
- **Captions are imperfect.** Auto-generated transcripts mangle proper nouns —
  "Leopold Aschenbrenner" comes through as "Liupold Ashen Brener", tickers get
  garbled. The extraction handles it, but verify any ticker before you trade it.
- **This is opinion, not data.** Every line on the dashboard is something a
  podcast host said. The timestamp exists so you can go check it yourself.
  Nothing here is a recommendation.
