"""Company roster: every company named across the analyzed episodes, in one list.

The dashboard is organised by episode. This is the orthogonal cut — organised by
company — so you can ask "who has been named, by whom, and why" without reading
seventeen cards.

    python roster.py                 # rebuild from data/signals.db
    python roster.py --min-mentions 2
    python roster.py --out data/roster.md

Two stages, deliberately split:

1. Aggregation (deterministic, no model). Every `items.entities_json` value is
   collected with the signals it appeared in, the channels that said it and the
   stances attached. Counts and attribution are computed from the database, so
   they are never something a model asserted.

2. Classification (one model pass per batch). The extractor's entity list mixes
   companies with people, places, process nodes and macro terms -- "SK hynix"
   sits next to "N7", "dioxin" and "Paul Volcker". The model decides which are
   investable operating companies, folds aliases onto one canonical name
   ("Hynix"/"SK Hynix"/"SK hynix"), and writes the one-line reason each was
   named, grounded only in the signal text it is shown.

The context line is a summary of what a podcast host said. It is not a view.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from engine.analyze import Analyzer
from engine.config import ROOT, Config, load_dotenv

# A fixed vocabulary. Batches are classified independently, so without an enum
# the model invents a new label per batch and the same sector ends up split
# across "Activist shorts", "Activist short sellers" and "Short sellers".
THEMES = [
    "AI labs & foundation models",
    "Semiconductors & AI silicon",
    "Memory & storage",
    "Foundry & packaging",
    "Optics, interconnect & networking",
    "Neoclouds & AI infrastructure",
    "Hyperscalers & big tech",
    "Software & developer tools",
    "Payments & fintech",
    "Banks, asset managers & private credit",
    "Hedge funds & trading firms",
    "Research publishers & activist shorts",
    "Space & defence",
    "Autos & autonomy",
    "Energy & power",
    "Industrials & waste-to-energy",
    "Healthcare & biopharma",
    "Consumer, media & gaming",
    "Exchanges & market infrastructure",
    "Robotics",
    "Other",
]

EMIT_ROSTER_TOOL = {
    "name": "emit_roster",
    "description": "Classify each supplied entity and describe why it was named.",
    "input_schema": {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "The entity name exactly as supplied to you.",
                        },
                        "is_company": {
                            "type": "boolean",
                            "description": (
                                "True only for an operating company, listed or private "
                                "(including funds and exchanges). False for people, "
                                "countries, cities, regulators, government bodies, "
                                "process nodes, products, indices and macro concepts."
                            ),
                        },
                        "canonical_name": {
                            "type": "string",
                            "description": (
                                "The company's common name, used to merge aliases: "
                                "'Hynix', 'SK Hynix' and 'SK hynix' all map to 'SK hynix'; "
                                "'NVDA' and 'Nvidia' both map to 'NVIDIA'."
                            ),
                        },
                        "ticker": {
                            "type": "string",
                            "description": (
                                "Ticker ONLY if it was named in the signal text, or the company "
                                "is a household-name large cap you are certain of. A wrong ticker "
                                "is worse than none -- when in doubt return an empty string."
                            ),
                        },
                        "listing": {
                            "type": "string",
                            "enum": ["public", "private", "subsidiary", "unknown"],
                        },
                        "theme": {"type": "string", "enum": THEMES},
                        "context": {
                            "type": "string",
                            "description": (
                                "ONE line, under 200 chars, saying why this company was named -- "
                                "the specific claim, number or role, drawn only from the signal "
                                "text supplied. Attribute to the speakers; never endorse. "
                                "If several episodes named it, give the dominant reason."
                            ),
                        },
                    },
                    "required": [
                        "name", "is_company", "canonical_name", "ticker", "listing",
                        "theme", "context",
                    ],
                },
            }
        },
        "required": ["entities"],
    },
}

# Merges the model keeps splitting across batches, because it sees "Meta" in one
# batch and "Facebook" in another with no shared context. Deterministic, so the
# same split does not have to be re-litigated on every rebuild.
CANONICAL_FIXES = {
    "meta platforms": "Meta",
    "facebook": "Meta",
    "anysphere": "Cursor",
    "alphabet inc.": "Alphabet",
    "google": "Alphabet",
    "x (twitter)": "X (Twitter)",
    "samsung": "Samsung Electronics",
    "sk hynix inc.": "SK hynix",
    "micron technology": "Micron",
}


def _clean_ticker(ticker: str, company: str) -> str:
    """Drop the tickers the model makes up when it has none.

    The failure mode is echoing the company name back ("xAI" as xAI's ticker),
    so anything that is not ticker-shaped is discarded. A missing ticker is
    cheap; a wrong one on a research desk is not.
    """
    t = (ticker or "").strip().upper()
    if not t or t == company.strip().upper():
        return ""
    root = t.split(".")[0]
    if not root.isalnum() or not 1 <= len(root) <= 6:
        return ""
    return t


PROMPT = """You are building a company roster for a hedge fund research desk from
signals already extracted from investing podcasts.

Below are entities named in those signals, each with the signal headlines and
bodies it appeared in. For every entity, decide whether it is an operating
company worth carrying on an investment roster, and if so write one line saying
why it was named.

Rules:
- `is_company` is false for people (Ken Griffin, Morris Chang), countries and
  cities, regulators, government bodies and self-regulatory organisations (SEC,
  DOJ, the Fed, FINRA, NY Attorney General), think tanks and research institutes,
  process nodes and products (N7, HBM3, CoWoS, ChatGPT, Claude Code), indices
  (S&P 500, QQQ, Mag 7) and macro concepts (inflation, AI capex, data centers).
  Funds, banks, exchanges and private companies ARE companies.
- A product, storefront or platform is not itself a company: set
  `canonical_name` to its owner (Steam -> Valve). Do the same for aliases and
  descriptive variants: 'situational awareness hedge fund' -> 'Situational
  Awareness'; 'SK Hynix' and 'Hynix' -> 'SK hynix'.
- `context` must come from the supplied signal text only. Do not add company
  facts from your own knowledge, and do not add a price target or a view.
- Attribute: "hosts argue", "Damodaran values", "cited as". Never state a
  podcast claim as fact.
- Keep `context` to one line, under 200 characters.
- Return every entity supplied, in the same order, whether or not it is a company.

---
ENTITIES
---
{block}
"""


def aggregate(db_path: Path) -> dict[str, dict]:
    """Collect every entity with the signals, channels and stances behind it."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT i.headline, i.body, i.type, i.stance, i.entities_json,
                  v.channel_name, v.title, v.published, v.url
           FROM items i JOIN videos v ON v.video_id = i.video_id
           ORDER BY v.published DESC"""
    ).fetchall()
    conn.close()

    agg: dict[str, dict] = defaultdict(
        lambda: {"mentions": 0, "channels": set(), "stances": [], "signals": [], "episodes": set()}
    )
    for r in rows:
        for name in json.loads(r["entities_json"] or "[]"):
            name = (name or "").strip()
            if not name:
                continue
            e = agg[name]
            e["mentions"] += 1
            e["channels"].add(r["channel_name"])
            e["stances"].append(r["stance"])
            e["episodes"].add((r["channel_name"], (r["published"] or "")[:10], r["title"], r["url"]))
            e["signals"].append(
                {"headline": r["headline"], "body": r["body"] or "", "type": r["type"],
                 "stance": r["stance"], "channel": r["channel_name"]}
            )
    return agg


def _block_for(name: str, e: dict, max_signals: int = 6, body_chars: int = 240) -> str:
    lines = [f"### {name}  (named in {e['mentions']} signals; channels: {', '.join(sorted(e['channels']))})"]
    for s in e["signals"][:max_signals]:
        lines.append(f"- [{s['type']}|{s['stance']}] {s['headline']}")
        lines.append(f"  {s['body'][:body_chars]}")
    return "\n".join(lines)


def classify(agg: dict[str, dict], analyzer: Analyzer, batch_size: int = 12) -> list[dict]:
    names = sorted(agg, key=lambda n: (-agg[n]["mentions"], n.lower()))
    out: list[dict] = []
    for i in range(0, len(names), batch_size):
        batch = names[i : i + batch_size]
        block = "\n\n".join(_block_for(n, agg[n]) for n in batch)
        print(f"  classify {i + 1}-{i + len(batch)} of {len(names)}", flush=True)
        try:
            res = analyzer._call(PROMPT.format(block=block), EMIT_ROSTER_TOOL)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! batch failed: {exc}")
            continue
        out.extend(res.get("entities", []) or [])
    return out


def merge(classified: list[dict], agg: dict[str, dict]) -> list[dict]:
    """Fold aliases onto the canonical name, unioning the deterministic counts."""
    by_canon: dict[str, dict] = {}
    for c in classified:
        if not c.get("is_company"):
            continue
        raw = c.get("name") or ""
        if raw not in agg:
            continue  # model invented a name it was not given
        canon = (c.get("canonical_name") or raw).strip() or raw
        canon = CANONICAL_FIXES.get(canon.lower(), canon)
        e = agg[raw]
        # Key on the lowercased name so "Z.ai" and "Z.AI" coming back from two
        # different batches land on one company; the spelling shown is taken
        # from whichever alias carried the most mentions, below.
        rec = by_canon.setdefault(
            canon.lower(),
            {"company": canon, "ticker": "", "listing": "unknown", "theme": "",
             "context": "", "mentions": 0, "channels": set(), "aliases": set(),
             "episodes": set(), "stances": []},
        )
        rec["mentions"] += e["mentions"]
        rec["channels"] |= e["channels"]
        rec["episodes"] |= e["episodes"]
        rec["stances"] += e["stances"]
        rec["aliases"].add(raw)
        # A ticker is a fact about the company, not about the alias that carried
        # it: "Facebook" may return META where "Meta" returned nothing. Take the
        # first one any alias supplies rather than only the top-mentioned one.
        rec["ticker"] = rec["ticker"] or _clean_ticker(c.get("ticker", ""), canon)
        # Keep the richest description: the alias with the most mentions wins.
        if e["mentions"] >= rec.get("_best", -1):
            rec["_best"] = e["mentions"]
            rec["company"] = canon
            rec["listing"] = c.get("listing") or rec["listing"]
            rec["theme"] = c.get("theme") or rec["theme"]
            rec["context"] = c.get("context") or rec["context"]

    for rec in by_canon.values():
        rec.pop("_best", None)
        rec["channels"] = sorted(rec["channels"])
        rec["aliases"] = sorted(rec["aliases"])
        rec["episodes"] = sorted(rec["episodes"], key=lambda t: t[1], reverse=True)
        st = [s for s in rec["stances"] if s]
        rec["stance_mix"] = {s: st.count(s) for s in sorted(set(st))}
        rec.pop("stances")
    return sorted(by_canon.values(), key=lambda r: (-r["mentions"], r["company"].lower()))


def to_markdown(recs: list[dict], stats: dict) -> str:
    lines = [
        "# Company roster",
        "",
        f"{len(recs)} companies named across {stats['episodes']} analyzed episodes "
        f"({stats['signals']} signals, {stats['channels']} channels).",
        "",
        "Context lines summarise what a speaker said. They are not views, and not "
        "recommendations. Verify any ticker before trading it — auto-generated "
        "captions mangle proper nouns.",
        "",
    ]
    by_theme: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        by_theme[r["theme"] or "Unsorted"].append(r)

    order = {t: i for i, t in enumerate(THEMES)}
    for theme in sorted(by_theme, key=lambda t: (order.get(t, len(THEMES)), t)):
        lines.append(f"\n## {theme}\n")
        lines.append("| Company | Ticker | Listing | Mentions | Named by | Why it came up |")
        lines.append("|---|---|---|---|---|---|")
        for r in by_theme[theme]:
            chans = ", ".join(c.replace(" Podcast", "") for c in r["channels"])
            lines.append(
                f"| **{r['company']}** | {r['ticker'] or '—'} | {r['listing']} | "
                f"{r['mentions']} | {chans} | {r['context']} |"
            )
    return "\n".join(lines) + "\n"


def to_html(recs: list[dict], stats: dict) -> str:
    """Artifact-ready fragment, in the dashboard's wire-service identity.

    Same palette, same three faces as engine/dashboard.py -- this is the same
    desk, cut by company instead of by episode. The structure differs because
    the job differs: the dashboard is read, this is scanned, so coverage weight
    is encoded as a rule and every filter is a chip.
    """
    import html as _h

    def esc(v: Any) -> str:
        return _h.escape(str(v or ""), quote=True)

    by_theme: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        by_theme[r["theme"] or "Other"].append(r)
    order = {t: i for i, t in enumerate(THEMES)}
    themes = sorted(by_theme, key=lambda t: (order.get(t, len(THEMES)), t))

    channels = sorted({c for r in recs for c in r["channels"]})
    top = max((r["mentions"] for r in recs), default=1)

    def short(ch: str) -> str:
        return ch.replace(" Podcast", "").replace("Aswath ", "")

    rows = []
    for theme in themes:
        items = by_theme[theme]
        rows.append(
            f'<section class="grp" data-theme-name="{esc(theme)}">'
            f'<h2 class="grp-h"><span>{esc(theme)}</span>'
            f'<span class="grp-n">{len(items)}</span></h2><div class="rows">'
        )
        for r in items:
            pct = max(4, round(100 * r["mentions"] / top))
            chans = "".join(f'<span class="ch">{esc(short(c))}</span>' for c in r["channels"])
            ep = r["episodes"][0] if r["episodes"] else None
            link = (
                f'<a class="ep" href="{esc(ep[3])}" target="_blank" rel="noopener">'
                f'latest: {esc(ep[1])} &middot; {esc(short(ep[0]))}</a>'
                if ep and ep[3] else ""
            )
            tick = f'<span class="tick">{esc(r["ticker"])}</span>' if r["ticker"] else ""
            alias = ""
            others = [a for a in r["aliases"] if a.lower() != r["company"].lower()]
            if others:
                alias = f'<span class="alias" title="also named as">aka {esc(", ".join(others))}</span>'
            rows.append(
                f'<article class="row" data-name="{esc((r["company"] + " " + r["ticker"] + " " + r["context"]).lower())}" '
                f'data-ch="{esc("|".join(r["channels"]))}" data-listing="{esc(r["listing"])}">'
                f'<div class="cnt"><span class="cnt-n">{r["mentions"]}</span>'
                f'<span class="bar"><i style="width:{pct}%"></i></span></div>'
                f'<div class="body"><h3 class="nm">{esc(r["company"])}{tick}'
                f'<span class="lst lst-{esc(r["listing"])}">{esc(r["listing"])}</span></h3>'
                f'<p class="ctx">{esc(r["context"])}</p>'
                f'<div class="meta">{chans}{alias}{link}</div></div></article>'
            )
        rows.append("</div></section>")

    chips = "".join(
        f'<button class="chip" data-ch="{esc(c)}" type="button">{esc(short(c))}</button>'
        for c in channels
    )

    return f"""<title>Signal Desk Roster</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400;6..72,600&family=Public+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
:root{{
  --ground:#F1F2F4; --surface:#FFFFFF; --surface-2:#F7F8F9; --line:#DEE1E6;
  --ink:#14181F; --ink-2:#3D4653; --ink-3:#6C7686;
  --accent:#B26A16; --accent-soft:#F5E7D4;
  --pub:#1F6F52; --priv:#6B4E9E; --sub:#2F5F8F; --unk:#5A6472;
  --shadow:0 1px 2px rgba(20,24,31,.05), 0 8px 24px -16px rgba(20,24,31,.28);
  --serif:"Newsreader",Georgia,"Times New Roman",serif;
  --sans:"Public Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}}
@media (prefers-color-scheme:dark){{
  :root:not([data-theme="light"]){{
    --ground:#0D1015; --surface:#161B22; --surface-2:#1C222B; --line:#2A323D;
    --ink:#E8EBEF; --ink-2:#B4BCC7; --ink-3:#828D9C;
    --accent:#E0A050; --accent-soft:#33261366;
    --pub:#4FB58A; --priv:#A98BDB; --sub:#6BA3DB; --unk:#8B95A3;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px -16px rgba(0,0,0,.7);
  }}
}}
:root[data-theme="dark"]{{
  --ground:#0D1015; --surface:#161B22; --surface-2:#1C222B; --line:#2A323D;
  --ink:#E8EBEF; --ink-2:#B4BCC7; --ink-3:#828D9C;
  --accent:#E0A050; --accent-soft:#33261366;
  --pub:#4FB58A; --priv:#A98BDB; --sub:#6BA3DB; --unk:#8B95A3;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px -16px rgba(0,0,0,.7);
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);
  font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1080px;margin:0 auto;padding:40px 24px 96px}}
header{{border-bottom:2px solid var(--ink);padding-bottom:18px;margin-bottom:22px}}
h1{{font-family:var(--serif);font-size:clamp(2rem,5vw,3rem);font-weight:600;
  letter-spacing:-.02em;margin:0;text-wrap:balance}}
.sub{{color:var(--ink-3);margin:6px 0 0;max-width:62ch}}
.stats{{display:flex;flex-wrap:wrap;gap:20px;margin-top:16px;
  font-family:var(--mono);font-size:11px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--ink-3);font-variant-numeric:tabular-nums}}
.stats b{{color:var(--ink);font-weight:600}}
.note{{background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--accent);
  border-radius:3px;padding:12px 16px;margin:0 0 24px;color:var(--ink-2);font-size:13.5px;
  box-shadow:var(--shadow)}}
.controls{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:8px}}
.chip,.seg{{font-family:var(--mono);font-size:11px;letter-spacing:.07em;text-transform:uppercase;
  background:var(--surface);color:var(--ink-2);border:1px solid var(--line);border-radius:3px;
  padding:7px 11px;cursor:pointer;transition:background .12s,border-color .12s,color .12s}}
.chip:hover,.seg:hover{{border-color:var(--accent);color:var(--ink)}}
.chip[aria-pressed="true"],.seg[aria-pressed="true"]{{background:var(--ink);color:var(--ground);
  border-color:var(--ink)}}
#q{{flex:1;min-width:220px;font-family:var(--mono);font-size:12px;padding:8px 12px;
  background:var(--surface);border:1px solid var(--line);border-radius:3px;color:var(--ink)}}
#q::placeholder{{color:var(--ink-3)}}
#q:focus,.chip:focus-visible,.seg:focus-visible,a:focus-visible{{outline:2px solid var(--accent);
  outline-offset:2px}}
.count{{font-family:var(--mono);font-size:11px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--ink-3);margin:14px 0 0}}
.grp{{margin-top:30px}}
.grp-h{{position:sticky;top:0;z-index:2;display:flex;justify-content:space-between;
  align-items:baseline;gap:12px;margin:0 0 2px;padding:10px 0 8px;background:var(--ground);
  border-bottom:1px solid var(--ink);font-family:var(--mono);font-size:11px;font-weight:600;
  letter-spacing:.11em;text-transform:uppercase;color:var(--ink)}}
.grp-n{{color:var(--ink-3);font-weight:400;font-variant-numeric:tabular-nums}}
.rows{{display:flex;flex-direction:column}}
.row{{display:grid;grid-template-columns:64px 1fr;gap:16px;padding:14px 0;
  border-bottom:1px solid var(--line)}}
.cnt{{display:flex;flex-direction:column;gap:5px;padding-top:3px}}
.cnt-n{{font-family:var(--mono);font-size:13px;font-weight:600;color:var(--ink-2);
  font-variant-numeric:tabular-nums}}
.bar{{display:block;height:3px;background:var(--line);border-radius:2px;overflow:hidden}}
.bar i{{display:block;height:100%;background:var(--accent)}}
.nm{{font-family:var(--serif);font-size:1.22rem;font-weight:600;letter-spacing:-.01em;
  margin:0;display:flex;flex-wrap:wrap;align-items:baseline;gap:9px;text-wrap:balance}}
.tick{{font-family:var(--mono);font-size:11px;font-weight:600;letter-spacing:.06em;
  color:var(--accent);border:1px solid var(--accent);border-radius:3px;padding:1px 5px}}
.lst{{font-family:var(--mono);font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;
  padding:2px 6px;border-radius:2px;color:var(--surface);font-weight:600}}
.lst-public{{background:var(--pub)}} .lst-private{{background:var(--priv)}}
.lst-subsidiary{{background:var(--sub)}} .lst-unknown{{background:var(--unk)}}
.ctx{{margin:5px 0 0;color:var(--ink-2);max-width:70ch}}
.meta{{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-top:7px;
  font-family:var(--mono);font-size:10.5px;letter-spacing:.05em;color:var(--ink-3)}}
.ch{{background:var(--surface-2);border:1px solid var(--line);border-radius:2px;padding:2px 6px;
  text-transform:uppercase}}
.alias{{font-style:italic}}
.ep{{color:var(--ink-3);text-decoration:none;border-bottom:1px solid var(--line)}}
.ep:hover{{color:var(--accent);border-color:var(--accent)}}
.empty{{padding:48px 0;text-align:center;color:var(--ink-3);font-family:var(--mono);font-size:12px}}
footer{{margin-top:44px;padding-top:16px;border-top:1px solid var(--line);color:var(--ink-3);
  font-size:12.5px;max-width:70ch}}
@media (max-width:560px){{.row{{grid-template-columns:48px 1fr;gap:12px}}}}
@media (prefers-reduced-motion:reduce){{*{{transition:none!important}}}}
</style>
<div class="wrap">
<header>
  <h1>Company Roster</h1>
  <p class="sub">Every company named across the tracked channels, cut by company
     instead of by episode.</p>
  <div class="stats">
    <span><b>{len(recs)}</b> companies</span>
    <span><b>{stats['episodes']}</b> episodes</span>
    <span><b>{stats['signals']}</b> signals</span>
    <span><b>{stats['channels']}</b> channels</span>
  </div>
</header>
<p class="note"><b>These are claims, not views.</b> Each line summarises what a
  speaker said on a podcast &mdash; not a recommendation, and not verified.
  Tickers are best-effort and blank where uncertain: check one before you trade it,
  because auto-generated captions mangle proper nouns.</p>
<div class="controls">
  <input id="q" type="search" placeholder="filter by company, ticker, claim&hellip;"
         aria-label="Filter companies">
  <button class="seg" id="pub" data-listing="public" type="button" aria-pressed="false">Public only</button>
</div>
<div class="controls">{chips}</div>
<p class="count" id="count"></p>
{''.join(rows)}
<p class="empty" id="empty" hidden>No company matches those filters.</p>
<footer>Built from transcripts of the tracked channels by
  <span style="font-family:var(--mono)">yt-signal-engine</span>. Mention counts are
  computed from the signal database, not asserted by a model; the one-line context
  is model-written from those signals and attributed to the speakers.</footer>
</div>
<script>
(function(){{
  var q=document.getElementById('q'), pub=document.getElementById('pub'),
      countEl=document.getElementById('count'), empty=document.getElementById('empty'),
      rows=[].slice.call(document.querySelectorAll('.row')),
      groups=[].slice.call(document.querySelectorAll('.grp')),
      chips=[].slice.call(document.querySelectorAll('.chip')),
      active=null, publicOnly=false;

  function apply(){{
    var term=q.value.trim().toLowerCase(), shown=0;
    rows.forEach(function(r){{
      var ok = (!term || r.dataset.name.indexOf(term)>-1)
            && (!active || r.dataset.ch.split('|').indexOf(active)>-1)
            && (!publicOnly || r.dataset.listing==='public');
      r.hidden=!ok; if(ok) shown++;
    }});
    groups.forEach(function(g){{
      g.hidden = !g.querySelectorAll('.row:not([hidden])').length;
    }});
    countEl.textContent = shown + (shown===1?' company':' companies') + ' shown';
    empty.hidden = shown>0;
  }}

  q.addEventListener('input', apply);
  pub.addEventListener('click', function(){{
    publicOnly=!publicOnly; pub.setAttribute('aria-pressed', publicOnly); apply();
  }});
  chips.forEach(function(c){{
    c.addEventListener('click', function(){{
      active = (active===c.dataset.ch) ? null : c.dataset.ch;
      chips.forEach(function(o){{ o.setAttribute('aria-pressed', o.dataset.ch===active); }});
      apply();
    }});
  }});
  apply();
}})();
</script>
"""


def main() -> int:
    load_dotenv()
    p = argparse.ArgumentParser(prog="roster")
    p.add_argument("--config", default=None)
    p.add_argument("--out", default="data/roster.md")
    p.add_argument("--min-mentions", type=int, default=1)
    p.add_argument("--batch-size", type=int, default=12)
    p.add_argument("--html", default=None, help="also write an artifact-ready HTML fragment")
    p.add_argument(
        "--from-json",
        default=None,
        help="re-render from a previous roster.json instead of re-classifying (no model calls)",
    )
    args = p.parse_args()

    cfg = Config.load(args.config)

    if args.from_json:
        src = Path(args.from_json)
        if not src.is_absolute():
            src = ROOT / args.from_json
        payload = json.loads(src.read_text(encoding="utf-8"))
        recs, stats = payload["companies"], payload["stats"]
        print(f"{len(recs)} companies loaded from {src}")
    else:
        agg = aggregate(Path(cfg.db_path))
        agg = {k: v for k, v in agg.items() if v["mentions"] >= args.min_mentions}
        print(f"{len(agg)} distinct entities from the knowledge base")

        analyzer = Analyzer(
            cfg.analyze.model, cfg.analyze.max_output_tokens, provider=cfg.analyze.provider
        )
        recs = merge(classify(agg, analyzer, args.batch_size), agg)
        print(f"{len(recs)} companies after alias merge")

        conn = sqlite3.connect(cfg.db_path)
        stats = {
            "episodes": conn.execute(
                "SELECT COUNT(*) FROM videos WHERE status='analyzed'"
            ).fetchone()[0],
            "signals": conn.execute("SELECT COUNT(*) FROM items").fetchone()[0],
            "channels": conn.execute(
                "SELECT COUNT(DISTINCT channel_name) FROM videos WHERE status='analyzed'"
            ).fetchone()[0],
        }
        conn.close()

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_markdown(recs, stats), encoding="utf-8")
    print(f"roster -> {out}")

    if not args.from_json:
        jpath = out.with_suffix(".json")
        jpath.write_text(
            json.dumps({"stats": stats, "companies": [
                {**r, "episodes": [list(e) for e in r["episodes"]]} for r in recs
            ]}, indent=2),
            encoding="utf-8",
        )
        print(f"json   -> {jpath}")

    if args.html:
        hpath = Path(args.html)
        if not hpath.is_absolute():
            hpath = ROOT / args.html
        hpath.parent.mkdir(parents=True, exist_ok=True)
        hpath.write_text(to_html(recs, stats), encoding="utf-8")
        print(f"html   -> {hpath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
