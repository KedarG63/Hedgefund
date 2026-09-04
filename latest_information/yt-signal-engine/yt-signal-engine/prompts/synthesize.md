You have the full set of signals extracted from one video on "{channel_name}".

Video: "{title}" (published {published})

Signals extracted across the episode:
{signal_block}

Chapter list as published by the creator:
{chapter_block}

Produce the top-of-card briefing that a reader sees before they decide whether
this episode is worth 40 minutes of their evening.

- **headline**: one line, under 90 characters, saying what actually happened or
  what the real claim is. Not the YouTube title — YouTube titles are written
  for clicks and routinely oversell. If the title says "NVIDIA Just Created the
  Next Recession" and the episode concludes the financing structure looks more
  like aircraft leasing than subprime, your headline says the latter.
- **summary**: 2–4 sentences. What is the argument, what is it based on, and
  where do the speakers hedge or disagree.
- **bullets**: 3–6 tight lines, each a distinct takeaway carrying its own
  specific — a number, a name, a mechanism. No filler bullets.
- **so_what**: one sentence on why this matters for someone making a position
  decision — or, honestly, that it does not and this one is background colour.
  Saying "no live implication" is a useful answer, not a failure.

Also **deduplicate and rank** the signals: merge items that are the same claim
seen from two slices, keep the version with the better quote, and drop anything
that on reflection is channel housekeeping or a sponsor read that slipped
through. Return the surviving items in priority order.

Return everything via the `emit_briefing` tool.
