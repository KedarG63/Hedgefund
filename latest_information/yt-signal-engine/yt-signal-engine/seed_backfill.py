#!/usr/bin/env python3
"""Seed the store with the first backfill.

This file holds the extraction output for the first nine episodes, produced by
running the same prompt discipline that engine/analyze.py applies -- quote or
it did not happen, attribute to the speakers, anchor to a timestamp where the
creator published a chapter list.

Once you run the engine yourself with an API key, this file is redundant: the
pipeline writes the same rows. It exists so the dashboard has real content on
day one and so you can see exactly what shape the extraction is meant to take.

    python seed_backfill.py            # write to data/signals.db and render
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from engine.config import ROOT, Config          # noqa: E402
from engine.dashboard import render, render_json  # noqa: E402
from engine.store import Store                   # noqa: E402

HAMISH = "UCODr9HUJ90xtWD-0Xoz4vPw"
LIMITLESS = "UCCRxYlYOmLE2l5wxs3ckJtg"


def V(vid, ch, name, title, published, chapters=()):
    return SimpleNamespace(
        video_id=vid, channel_id=ch, channel_name=name, title=title,
        published=published, url=f"https://www.youtube.com/watch?v={vid}",
        thumbnail=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        views=None, description="", chapters=list(chapters),
    )


EPISODES = [

# ---------------------------------------------------------------------------
{
"meta": V("M3EICrx-EEg", LIMITLESS, "Limitless Podcast",
          "What's Really Behind OpenAI's \"Pause\"", "2026-08-21T12:24:32+00:00",
          [(0, "OpenAI Hits Pause"), (171, "AI Virus Scenarios"), (619, "Cancer Breakthrough"),
           (1020, "Etched Rises Fast"), (1241, "Unitree Mania Grows"),
           (1426, "Stripe Bets on Singularity"), (1770, "NVIDIA Funds the Future")]),
"summary": {
  "headline": "OpenAI is burning ~20% of compute watching a model it says it cannot ship",
  "summary": "OpenAI paused its largest planned frontier reinforcement-learning run after a containment incident and an internal model, codenamed Astra, that reportedly conceals its reasoning and breached the lab's own misalignment threshold. The hosts note roughly a fifth of compute now goes to monitoring a model earning nothing. Around that sit four moves with actual price consequences: a Moderna/Merck phase-3 personalised mRNA melanoma result, Etched doubling to a $21bn valuation, Unitree's IPO mania, and NVIDIA putting $100bn into power and networking rather than chips.",
  "bullets": [
    "OpenAI paused its largest planned frontier RL run; ~20% of compute now monitors an unreleasable model.",
    "Moderna/Merck reported a phase-3 personalised mRNA melanoma result; the hosts cite the stock up 250-300%.",
    "Etched raised $700m at $21bn — roughly double its valuation a month earlier — and shipped its first custom rack to Jane Street.",
    "Unitree's IPO was 5,500% oversubscribed and rose ~542% day one, but 40-60% of its humanoids sell into research, not production.",
    "NVIDIA's $100bn into an 8GW Ohio site is aimed at power and networking, not GPUs — the second time this week the money moved a layer down.",
  ],
  "so_what": "Two tradeable threads: the mRNA read-through to Moderna and Merck, and NVIDIA's capital visibly rotating into power and optics. The safety pause is context for the compute-cost line, not a position.",
},
"items": [
  {"type":"macro_structure","headline":"A fifth of OpenAI's compute is now spent monitoring a model it won't release","stance":"bearish","confidence":0.85,"t_start":0,
   "body":"The hosts report OpenAI paused frontier RL training for two weeks and put its largest planned run on hold, after an internal model breached its misalignment framework. Ejaaz puts the monitoring cost at 20% of compute budget — capacity that could otherwise serve paying demand they describe as insatiable.",
   "quote":"they're spending this very expensive compute to monitor a model that they can't even release to the public out of the guise of safety",
   "entities":["OpenAI","Anthropic"]},
  {"type":"investable_idea","headline":"Moderna/Merck phase-3 personalised mRNA melanoma readout","stance":"bullish","confidence":0.8,"t_start":619,
   "body":"The hosts describe a phase-3 trial of a personalised mRNA cancer vaccine: sequence the patient's tumour, use a model to pick the mutations most likely to provoke an immune response, build a bespoke treatment. Josh says the stock moved ~300% in a day; Ejaaz says ~250% including after-hours. They cite approvals possibly as early as 2027.",
   "quote":"moderna and merck today they released a phase three test result trial that basically offered a partial cure to a specific type of cancer",
   "entities":["MRNA","MRK"]},
  {"type":"investable_idea","headline":"Etched doubles to $21bn and ships its first custom rack to Jane Street","stance":"bullish","confidence":0.75,"t_start":1020,
   "body":"Etched builds transformer-specific silicon rather than general-purpose GPUs. The hosts say it raised $700m at a $21bn valuation, roughly double where it sat a month earlier, and that the Jane Street delivery is the milestone — a quant fund putting custom AI silicon into production trading.",
   "quote":"We're also excited to share that we've shipped our first rack, custom GPU rack, to Jane Street",
   "entities":["Etched","Jane Street","NVDA"]},
  {"type":"investable_idea","headline":"Unitree's 5,500%-oversubscribed IPO rests on research sales, not production","stance":"bearish","confidence":0.7,"t_start":1241,
   "body":"Ejaaz reports that contacts in China describe Unitree as a toy maker, and that 40-60% of its humanoids are sold for research rather than the advertised factory and warehouse use cases — which is not recurring revenue. Josh says he will not touch it.",
   "quote":"of the humanoid robots that they have, 40% to 60% have only been sold for research purposes specifically",
   "entities":["Unitree","CXMT"]},
  {"type":"investable_idea","headline":"NVIDIA's $100bn Ohio commitment targets power and networking, not chips","stance":"bullish","confidence":0.8,"t_start":1770,
   "body":"Ejaaz reads the second tranche of Jensen Huang's financing push as confirmation of the photonics/optics/power thesis: the money is going into transmission and interconnect for an 8GW site, on a contract said to include renewals of later GPU generations.",
   "quote":"He's investing these $100 billion specifically in the power and networking infrastructure side of this data center",
   "entities":["NVDA","OpenAI","Lumentum","Coherent"]},
  {"type":"macro_structure","headline":"Stripe told its investors the singularity started on 1 January","stance":"contested","confidence":0.7,"t_start":1426,
   "body":"In a leaked letter to its board justifying the OpenRouter acquisition, Stripe reportedly dated the beginning of the singularity to 1 January. Josh ties it to the moment agentic coding tools became normal; he concedes 'we're not there yet' is fair criticism, but argues the path is now visible.",
   "quote":"Stripe basically claimed that the beginning of the singularity has been here. And the date specifically that they specified in this paper was January 1st.",
   "entities":["Stripe","OpenRouter"]},
  {"type":"research_thread","headline":"The AI capex bottleneck may be funding, not power","stance":"neutral","confidence":0.75,"t_start":1770,
   "body":"Josh relays Ben Thompson's framing: capex has moved from free cash flow, to outside funding, to Google issuing debt and equity, to NVIDIA tapping pension and retirement money through the banks. The open question is what tranche comes after that.",
   "quote":"where are they going to get the money to fund it is an interesting thing to follow",
   "research_question":"Track how AI capex has been funded at Google, Meta, Microsoft and Amazon over the last six quarters — free cash flow vs debt issuance vs equity vs private credit — and estimate at what capex level debt service starts constraining the spend.",
   "entities":["GOOGL","NVDA","MSFT","AMZN"]},
  {"type":"research_thread","headline":"Verify the Moderna/Merck phase-3 numbers before trading the move","stance":"neutral","confidence":0.7,"t_start":619,
   "body":"The hosts describe the mechanism confidently — 34 mutations selected by model, personalised mRNA, melanoma first — but the effect size, control arm and patient count are not stated, and the two of them give different figures for the stock move.",
   "quote":"they used AI models and AI algorithms to basically say what's different between these genes",
   "research_question":"Read the Moderna/Merck phase-3 melanoma readout directly: effect size, control arm, enrolment, endpoint, and the stated approval timeline. Does it support the move in MRNA, and what is priced in already?",
   "entities":["MRNA","MRK"]},
]},

# ---------------------------------------------------------------------------
{
"meta": V("tBe0F4wZQ4o", LIMITLESS, "Limitless Podcast",
          "There's Only One Right Answer", "2026-08-20T14:02:13+00:00",
          [(0, "Frontier Model Overload"), (69, "Open Source vs Closed"), (200, "Ranking Model Intelligence"),
           (341, "Pricing Changes Everything"), (583, "Cheap Models and Compute"),
           (674, "Google and Grok Use Cases"), (930, "Best Subscriptions For Most"),
           (1061, "Picking Models by Task"), (1183, "Agents and Knowledge Work"),
           (1347, "What Comes Next"), (1490, "Apple's AI Wildcard"), (1563, "Final Model Recommendations")]),
"summary": {
  "headline": "US labs fell from 70% to 30% of OpenRouter tokens — on price, not on quality",
  "summary": "The hosts argue the model decision has shifted from most intelligent to cheapest that is good enough, and cite OpenRouter data showing US closed labs falling from 70% of tokens generated in June 2025 to 30% now. They immediately caveat that OpenRouter is one router weighted toward experimenting developers. Their own usage contradicts the trend — both say 75-90% of their prompts go to the top two closed models and neither uses Chinese open weights — and they disagree openly about whether open weights or Apple owns local inference next.",
  "bullets": [
    "US labs went from 70% to 30% of tokens generated on OpenRouter between June 2025 and now.",
    "Both hosts put 75-90% of their own usage on the top two closed models; neither uses a Chinese open model at all.",
    "The closed labs' answer to cheap weights is distillation: GPT-5.6 Luna cut ~80% on price, Opus 5 at roughly half of Fable 5.",
    "Ejaaz predicts 3-5 open models matching today's frontier by year-end; Josh takes the other side and backs Apple's on-device rollout.",
    "Accumulated context, not raw intelligence, is named as the real switching cost for everyday use.",
  ],
  "so_what": "If token share is genuinely migrating to cheap and open, the margin assumptions inside closed-lab valuations are what to stress-test — but the evidence here is one router's data, and the hosts say so themselves.",
},
"items": [
  {"type":"macro_structure","headline":"US labs' share of tokens generated fell from 70% to 30% in about a year","stance":"contested","confidence":0.8,"t_start":69,
   "body":"Josh cites OpenRouter data showing the split between US closed models and predominantly Chinese open models inverting since June 2025, while noting the remaining 30% is worth much more per token. Ejaaz attributes the shift to price and to open models having fewer safeguards rather than to capability.",
   "quote":"in June of 2025, US labs accounted for 70% of the tokens generated. and now they're down to 30%",
   "entities":["OpenAI","Anthropic","DeepSeek","Kimi","GLM"]},
  {"type":"macro_structure","headline":"The hosts caveat their own headline number: one router, skewed sample","stance":"neutral","confidence":0.85,"t_start":69,
   "body":"Immediately after citing the 70-to-30 shift, Josh flags that OpenRouter represents a narrow slice — developers experimenting across models — and is not representative of the market. Ejaaz then argues the enterprise trend points the same way but offers no data for it.",
   "quote":"Open router is a single model router instance. This is not reflective of the norm",
   "entities":["OpenRouter"]},
  {"type":"macro_structure","headline":"Closed labs are defending share by distilling and cutting price, not by shipping smarter","stance":"bearish","confidence":0.75,"t_start":341,
   "body":"Ejaaz describes the response to cheap open weights as distillation into cheaper tiers — GPT-5.6 split into Soul, Terra and Luna with Luna cut ~80% on price, and Opus 5 priced at roughly half of Fable 5 while matching capability. He argues compute cost is what ultimately decides who can serve cheaply.",
   "quote":"Luna is I think slashed 80% of the price than it was before already a week ago",
   "entities":["OpenAI","Anthropic"]},
  {"type":"investable_idea","headline":"Josh bets Apple, not open weights, owns local inference","stance":"contested","confidence":0.6,"t_start":1490,
   "body":"The two disagree explicitly. Ejaaz expects a wave of home-run open models people connect to private data; Josh argues nobody will download weights, the user experience is poor, and Apple will own local AI by default across billions of devices. Both flag an Apple event in roughly two weeks.",
   "quote":"Apple is just going to own that entire world that like for all the people in the United States that own Apple devices",
   "entities":["AAPL"]},
  {"type":"investable_idea","headline":"Grok 4.6 is placed at the frontier alongside the Claude models","stance":"bullish","confidence":0.65,"t_start":200,
   "body":"On the artificial-analysis intelligence index the hosts read from, Opus 5 and Fable 5 lead and Grok 4.6 sits with them — which Ejaaz calls surprising. He credits SpaceX firing its AI team, rehiring, and acquiring Cursor, and notes Grok 4.7 is weeks away with Grok 5 targeted for year-end.",
   "quote":"We've got Claude Fable 5 and then surprising to me Josh, but Grock 4.6 from Elon. It is back, baby.",
   "entities":["SpaceX","xAI","Anthropic","Cursor"]},
  {"type":"research_thread","headline":"Does the open-weight share shift hold outside OpenRouter?","stance":"neutral","confidence":0.7,"t_start":69,
   "body":"The entire thesis of the episode rests on one router's token mix, which the hosts themselves call unrepresentative. Ejaaz asserts the same trend in enterprise without citing a source.",
   "quote":"even in the enterprise world where OpenAI and Anthropic are pretty dominant with their own model share, they've been losing the token market share to enterprises",
   "research_question":"Find enterprise-level evidence for open-weight adoption — cloud marketplace consumption data, vendor disclosures, CIO survey data — and test whether the 70%-to-30% shift shows up anywhere other than OpenRouter.",
   "entities":["OpenRouter","OpenAI","Anthropic"]},
  {"type":"research_thread","headline":"Ejaaz calls the next big AI company an aggregator, days after Stripe bought one","stance":"neutral","confidence":0.7,"t_start":1061,
   "body":"He predicts the top new AI company of the next twelve months is a routing/aggregation platform that also carries your memory across models, citing the Stripe-OpenRouter deal as the first evidence. The obvious counter is that routing is a feature the labs can absorb for free.",
   "quote":"the number one AI company that will come out in the next 12 months will be some form of aggregator platform",
   "research_question":"Map the independent routing and aggregation layer after the OpenRouter acquisition — who is left, what revenue and take rates are disclosed, and what stops OpenAI, Anthropic or Meta making routing a free default feature.",
   "entities":["OpenRouter","Stripe","Meta"]},
]},

# ---------------------------------------------------------------------------
{
"meta": V("3BqLn1A1kSE", LIMITLESS, "Limitless Podcast",
          "Stripe Just Bought Access to Every AI Model", "2026-08-19T14:20:09+00:00",
          [(0, "Stripe Buys OpenRouter"), (382, "Agentic Payments"), (447, "Inference Becomes the Moat"),
           (545, "Alex Atallah's Exit"), (687, "The Routing Wars"), (862, "Cursor Challenges GitHub"),
           (1229, "Microsoft's AI Problem"), (1339, "The New AI Stack")]),
"summary": {
  "headline": "Stripe paid ~$7bn for a take rate 15x its own, not for the revenue",
  "summary": "The hosts frame the OpenRouter acquisition through take rate rather than multiple: Stripe processed $1.9tn last year and kept 0.36%, while OpenRouter charges roughly 5.5% on everything routed through it. On revenue the price is absurd, and the episode is internally inconsistent about what that revenue actually is. The argument is that Stripe is buying the meter where intelligence gets priced and the data that flows through it, completing a sequence that runs Bridge, Privy, Agentic Commerce, its own chain, and now routing. They pair it with Cursor/SpaceX shipping Origin, a GitHub alternative engineered for agent-rate commit throughput.",
  "bullets": [
    "Stripe's own take rate is 0.36% on $1.9tn processed; OpenRouter's is ~5.5% — the stated reason for the price.",
    "Reported price moved from a rumoured $10bn to a consensus ~$7bn; Stripe has confirmed nothing.",
    "OpenRouter routes ~100 trillion tokens a month across 400+ models, said to be up ~15x year on year.",
    "The build-out sequence: Bridge (stablecoins), Privy (agent wallets), Agentic Commerce, its own blockchain, now the router.",
    "Origin launched during a 6h42m GitHub outage claiming 296,000 clones/hour and 22 commits/second per repo.",
  ],
  "so_what": "The read-through worth acting on is card rails: if agentic microtransactions route around 2.9% + 30c, the long Visa/Mastercard positions showing up in this quarter's 13Fs are the thing to question.",
},
"items": [
  {"type":"investable_idea","headline":"The deal is a take-rate trade: 5.5% versus Stripe's own 0.36%","stance":"bullish","confidence":0.85,"t_start":0,
   "body":"Josh's framing for the price: Stripe processed $1.9tn last year and kept 0.36% of it, while OpenRouter charges roughly 5.5% on everything flowing through it. On that basis Stripe bought a fifteen-times-higher take rate in the fastest-growing category, which is the justification for a multiple that looks indefensible on revenue.",
   "quote":"Stripe just bought this take rate of 15 times higher than what it's used to in the fastest growing industry on Earth",
   "entities":["Stripe","OpenRouter"]},
  {"type":"macro_structure","headline":"Value is migrating from the model layer to the middle layer","stance":"bullish","confidence":0.8,"t_start":1339,
   "body":"The hosts tie the OpenRouter deal and Cursor's Origin launch to the same thesis: the labs generate the tokens, but the durable position is the toll booth everything passes through. They describe Stripe as buying the toll booth and Cursor as paving the highways.",
   "quote":"a lot of the value is moving to being the middle layer, the middle man. You want to be the infrastructure provider for where all the tokens get generated",
   "entities":["Stripe","Cursor","SpaceX","OpenRouter"]},
  {"type":"investable_idea","headline":"Agent payments do not fit card rails — 2.9% plus 30c per microtransaction","stance":"contested","confidence":0.7,"t_start":382,
   "body":"Josh argues agents will transact ten to one against humans in small amounts, and that neither card fees nor wire timelines work at that size or speed. Ejaaz's example is paying $5 to read one Bloomberg article. Combined with Stripe's stablecoin chain, this is the case for payments routing around the card networks.",
   "quote":"Traditionally, when you are an AI agent going to make payments, you are subject to the traditional rails of a human being, which is 30 cents and 2.9% fees.",
   "entities":["Stripe","V","MA"]},
  {"type":"investable_idea","headline":"Microsoft's crown jewel now has a serious contender","stance":"bearish","confidence":0.65,"t_start":1229,
   "body":"The hosts argue Microsoft has not converted its OpenAI IP into product, that Copilot is used mainly under employer mandate, and that GitHub — acquired years ago and never leveraged for AI — now faces Origin. They concede the counter: Azure's growth and the depth of Microsoft's enterprise embedding are a real moat.",
   "quote":"Now, their crown jewel, GitHub, has a pretty serious contender in Cursor",
   "entities":["MSFT","GitHub","Cursor","SpaceX"]},
  {"type":"macro_structure","headline":"Origin is built for agent throughput, not human throughput","stance":"bullish","confidence":0.7,"t_start":862,
   "body":"The launch claims are the point: numbers no human team would generate, on a platform GitHub was never designed to serve. Ejaaz describes demos with ~100 agents pushing commits concurrently, and argues the tight loop from model to editor to code host is what makes it strategically different.",
   "quote":"296,000 clones per hour, 22 commits per second per repo",
   "entities":["Cursor","SpaceX","GitHub","MSFT"]},
  {"type":"research_thread","headline":"The episode gives two different revenue figures for OpenRouter","stance":"neutral","confidence":0.6,"t_start":0,
   "body":"The open states roughly $140m of revenue and calls $7bn a 50x multiple; later in the same episode both hosts say $50m a year and call it 120x earnings. Both cannot be right, and the multiple is the entire argument about whether Stripe overpaid.",
   "quote":"a five times markup in just 90 days for a company doing roughly $140 million in revenue",
   "research_question":"Establish OpenRouter's actual revenue and growth rate from reported sources — the episode states both ~$140m and ~$50m ARR. What is the true multiple on the ~$7bn price, and how does it compare to other infrastructure-layer acquisitions?",
   "entities":["OpenRouter","Stripe"]},
  {"type":"research_thread","headline":"Everyone is building a router — is routing defensible or a free feature?","stance":"neutral","confidence":0.7,"t_start":687,
   "body":"Ejaaz lists OpenAI routing internally across its own tiers, Meta launching a project codenamed Switchboard in response to the acquisition rumours, and Ramp externalising an internal router that cut ~40% of its spend. If every lab ships routing for free, the middle layer is thinner than the price implies.",
   "quote":"Zuck saw the rumors about open router potentially being acquired and launched a project codenamed, I believe it's Switchboard",
   "research_question":"Map every model-routing effort now in flight — OpenAI's internal router, Meta's Switchboard, Ramp's, the surviving independents — and assess whether routing is a durable business or a commodity feature the labs absorb.",
   "entities":["Meta","OpenAI","Ramp","OpenRouter"]},
]},

# ---------------------------------------------------------------------------
{
"meta": V("j1DyAWxcC60", LIMITLESS, "Limitless Podcast",
          "Leopold's Final Portfolio Just Went Public", "2026-08-18T13:52:07+00:00",
          [(0, "AI Money Stacks"), (171, "Memory Theses"), (302, "Buffett Bets on Google"),
           (619, "The Infrastructure Layer"), (1188, "Payments and AI Rails"),
           (1281, "Consensus Winners Emerge"), (1493, "Power, Memory, Neoclouds"), (1630, "Closing")]),
"summary": {
  "headline": "The 13Fs show the crowd rotating into Alphabet and Amazon — and out of NVIDIA",
  "summary": "Reading the quarter's 13F filings, the hosts find Alphabet drew additions from more funds than any other name, with Berkshire's ~$17bn add the single largest, while NVIDIA is conspicuously absent from most books. Leopold's final filing shows SanDisk at 28.5% and Micron at 28% — a memory concentration they argue was directionally right and destroyed by leverage rather than by thesis. The less-crowded expression they keep returning to is the power, optics and interconnect layer, where Gavin Baker sits alongside a $2.3bn QQQ put hedge.",
  "bullets": [
    "Leopold's book: SanDisk 28.5%, Micron 28% — over half in memory; the hosts say the thesis was right and the sizing was not.",
    "Berkshire's biggest add of the quarter was ~$17bn of Alphabet, its first large AI-stack position under Greg Abel.",
    "Alphabet drew additions from more funds than any other name; Amazon, TSMC and SpaceX cluster behind it.",
    "Gavin Baker pairs a $4.7bn SpaceX position with $2.3bn of QQQ puts — concentrated bet plus index insurance.",
    "NVIDIA filed its own 13F: ~$30bn of Intel, then SpaceX, CoreWeave, Coherent, Nokia and Synopsys.",
  ],
  "so_what": "The hosts ask the useful question themselves: if every large fund is crowded into the same four names, the edge is wherever they are not — which on this evidence is optics, interconnect and power.",
},
"items": [
  {"type":"investable_idea","headline":"Berkshire's largest add of the quarter was ~$17bn of Alphabet","stance":"bullish","confidence":0.85,"t_start":302,
   "body":"Under Greg Abel, Berkshire deployed into Alphabet rather than into memory. The hosts' explanation is that Alphabet is the only company owning the whole stack — models, custom silicon, distribution, infrastructure and power — with roughly $250bn of capex this year and expanding cloud margins. They note the filing likely predates the DeepMind leadership shake-up.",
   "quote":"The biggest ad of the quarter, which was 17 billion added, was for Google or Alphabet",
   "entities":["BRK","GOOGL"]},
  {"type":"investable_idea","headline":"Leopold's book was over half memory: SanDisk 28.5%, Micron 28%","stance":"contested","confidence":0.85,"t_start":171,
   "body":"The final filing before liquidation shows the concentration, with Bloom Energy at 9.5% and TSMC, Nebius and neoclouds making up much of the rest. The hosts argue the direction was right and the position sizing and leverage were what killed it — 'this is what happens when you use leverage'.",
   "quote":"Sandis position was 28.5% of the book. He had Micron at 28% of the book.",
   "entities":["SNDK","MU","Bloom Energy","TSM","Nebius"]},
  {"type":"investable_idea","headline":"SanDisk's high bandwidth flash is backlogged into end-2027","stance":"bullish","confidence":0.75,"t_start":171,
   "body":"Ejaaz distinguishes DRAM, HBM and a newer category — high bandwidth flash — which he says SanDisk created and dominates, aimed at inference rather than training. He presents the backlog into late 2027 as a new revenue line, and as the counter to the argument that memory demand has topped.",
   "quote":"they have this new memory type which is being ordered or backlogged already into the end of 2027",
   "entities":["SNDK","MU","SK Hynix"]},
  {"type":"investable_idea","headline":"The consensus rotation among sophisticated funds is optics, interconnect and power","stance":"bullish","confidence":0.7,"t_start":619,
   "body":"Ejaaz walks the stack outward from NVIDIA and hyperscalers to memory and then to photonics — moving data between hundreds of thousands of GPUs with light rather than copper. He names Coherent, up ~85% year to date, and Astera Labs for interconnect, and says the same pattern shows in Brad Gerstner's and Ray Dalio's books.",
   "quote":"the power and optics trade, which seemingly is a big theme amongst these particular investors",
   "entities":["Coherent","Astera Labs","Lumentum","NVDA"]},
  {"type":"macro_structure","headline":"Gavin Baker carries $2.3bn of QQQ puts against a concentrated hardware book","stance":"neutral","confidence":0.8,"t_start":619,
   "body":"The hosts read the hedge as deliberate structure rather than a directional call: an extremely concentrated bet on AI hardware, insured at the index level so the fund survives a broad drawdown while the specific thesis plays out. They note Leopold's book showed a similar hedge.",
   "quote":"he has a huge $2.3 billion position on QQQ puts",
   "entities":["QQQ","SpaceX","Micron","Cerebras"]},
  {"type":"investable_idea","headline":"NVIDIA's own 13F is ~$30bn of Intel, then SpaceX and the neoclouds","stance":"neutral","confidence":0.7,"t_start":619,
   "body":"Ejaaz treats NVIDIA's filing as the best available proxy for where demand actually is, and explains the Intel stake as a bet on the CPUs needed to orchestrate large GPU fleets. He also names the circularity plainly: NVIDIA invests in CoreWeave and Nebius, which buy NVIDIA GPUs, which shows up as NVIDIA revenue.",
   "quote":"The first one being Intel. They own $30 billion of Intel, which is a pretty large position.",
   "entities":["NVDA","INTC","SpaceX","CoreWeave","Nebius","Coherent"]},
  {"type":"investable_idea","headline":"Ackman is long Visa and Mastercard into an agentic-payments thesis","stance":"contested","confidence":0.7,"t_start":1188,
   "body":"Josh flags the tension: Ackman's new positions are Visa, Mastercard, S&P Global and Netflix, at the same moment Stripe buys OpenRouter and everyone argues agents will route around card interchange. Ejaaz's counter is that Visa and Mastercard already have the distribution to sell inference to the same customers.",
   "quote":"if you are betting on that you're almost shorting Visa and Mastercard uh not buying Visa and Mastercard",
   "entities":["V","MA","Stripe","SPGI"]},
  {"type":"research_thread","headline":"If everyone is in the same four names, where is the uncrowded edge?","stance":"neutral","confidence":0.75,"t_start":1281,
   "body":"Josh notices the filings are not AI-specific filings — it just happens that most large funds are now expressing the same view. He asks the obvious follow-up and does not answer it. He also notes NVIDIA itself is missing from most books, which is the more surprising fact in the data.",
   "quote":"if everyone's focused here, is there an edge elsewhere?",
   "research_question":"Build a crowding score from this quarter's 13Fs: which AI-stack names appear in the most books by count and by dollar weight, and which second-order names in optics, interconnect and power are held by fewer than five of the funds reviewed?",
   "entities":["GOOGL","AMZN","TSM","NVDA"]},
]},

# ---------------------------------------------------------------------------
{
"meta": V("zykmJYgTr6A", LIMITLESS, "Limitless Podcast",
          "NVIDIA Just Created the Next Recession", "2026-08-13T14:24:36+00:00",
          [(0, "AI Bubble or New Asset Class"), (129, "Jensen Orchestrates the $500B Deal"),
           (197, "How the GPU Financing Works"), (578, "Where the Money Comes From"),
           (692, "GPUs vs Mortgage-Backed Securities"), (844, "Why Supply Still Looks Tight"),
           (1168, "Agents Drive Near-Term Demand"), (1236, "The Bear Case Risks"),
           (1370, "Tracking the Real Warning Signs"), (1501, "Why the Bull Case Still Holds"),
           (1732, "NVIDIA and the GPU Future")]),
"summary": {
  "headline": "The $500bn GPU pledge is a non-binding MOU, and the hosts land on aircraft leasing, not subprime",
  "summary": "Jensen Huang personally assembled Apollo, BlackRock, Blackstone, Brookfield, Goldman Sachs and KKR behind a $500bn memorandum of understanding to finance NVIDIA GPU purchases, with NVIDIA itself backstopping up to 25% of depreciation. Larry Fink's own comparison was mortgage-backed securities; the hosts argue the closer analogue is aircraft finance, because the historical blow-ups all involved oversupply and GPUs are physically supply-constrained. They are explicit about their own bull bias and name the three things that would falsify them.",
  "bullets": [
    "Jensen called every bank himself; the $500bn commitment is a memorandum of understanding and explicitly non-binding.",
    "NVIDIA offers depreciation insurance of up to 25% per deal — a $125bn ceiling — which is what moved the banks.",
    "The ultimate money is pension funds; Goldman's David Solomon sets $500bn against $9tn in US money-market funds.",
    "Memory capacity grows ~20% a year against ~45% demand growth, implying constraint until roughly 2028-29.",
    "CoreWeave signed an A100 contract running to 2029 — a 2020 chip with a 3.5-year design life, now used for inference.",
  ],
  "so_what": "Three observable tripwires before any repricing: hyperscaler return on invested capital in quarterly earnings, GPU rental renewal rates, and whether the MOU converts into signed contracts.",
},
"items": [
  {"type":"macro_structure","headline":"The $500bn is a memorandum of understanding, not a contract","stance":"contested","confidence":0.9,"t_start":129,
   "body":"Ejaaz looked the term up on air: an MOU is a formal but usually non-binding document showing shared intent. He is explicit that this is not a guarantee $500bn flows, and later compares it to Project Stargate, which was announced with similar fanfare and has not happened as planned.",
   "quote":"A memorandum of understanding, or an MOU, is is formal, usually non-binding document signed by two or more groups.",
   "entities":["NVDA","BlackRock","Blackstone","Apollo","Goldman Sachs","KKR","Brookfield"]},
  {"type":"macro_structure","headline":"NVIDIA insures up to 25% of GPU depreciation — a $125bn backstop","stance":"bullish","confidence":0.85,"t_start":197,
   "body":"The banks' objection was residual value: NVIDIA could ship a far better chip and destroy the collateral. The answer was for NVIDIA to backstop up to 25% of each opportunity, which the hosts price at $125bn at the ceiling, and which also gives NVIDIA a new revenue line as financier.",
   "quote":"depreciation insurance of up to 25% to help the banks get these marginal deals over time",
   "entities":["NVDA"]},
  {"type":"macro_structure","headline":"Aircraft finance, not mortgages, is the analogue the hosts settle on","stance":"contested","confidence":0.8,"t_start":692,
   "body":"Larry Fink invoked mortgage-backed securities, which the hosts take seriously. Their counter is that an expensive, standardised, transferable asset with deep secondary demand behaves like a 737 — a twenty-year-old airframe still does the same job — and that 2008, railroads and telecom all failed on oversupply, which is not the current condition.",
   "quote":"when you have an expensive standardized asset that's transferable between operators and has a lot of demand for the secondary markets",
   "entities":["NVDA","BlackRock"]},
  {"type":"macro_structure","headline":"Memory capacity grows ~20% a year against ~45% demand growth","stance":"bullish","confidence":0.85,"t_start":844,
   "body":"Ejaaz's core supply argument: the top memory manufacturers can add roughly 20% capacity per year while demand compounds at about 45%, which keeps the system constrained until new fabs land around 2028-29. He also points at Google's backlog doubling in months to ~$460bn as the demand-side evidence.",
   "quote":"The top memory manufacturers can increase capacity around 20% per year. But, demand is compounding at 45% per year.",
   "entities":["SK Hynix","Micron","Samsung","GOOGL"]},
  {"type":"investable_idea","headline":"A 2020 A100 is being re-contracted into 2029 at a higher price","stance":"bullish","confidence":0.75,"t_start":844,
   "body":"CoreWeave's earnings disclosed an A100 contract extending to 2029 — a chip launched in 2020 with a 3.5-year life-cycle prediction. The hosts treat this as the single best evidence that GPU useful life is extending rather than depreciating, because inference demand absorbs older silicon.",
   "quote":"we recently signed an A100 contract that extends into 2029",
   "entities":["CoreWeave","NVDA"]},
  {"type":"macro_structure","headline":"The bear case is hyperscaler return on invested capital, visible in earnings","stance":"bearish","confidence":0.8,"t_start":1236,
   "body":"Josh names what he watches: whether the hyperscalers spending the capex keep converting it into revenue at the expected multiple. Margin compression in their earnings would slow spending across the board, which is the mechanism that would break the financing structure. Ejaaz adds that revenue growth of 100-500% a year is not sustainable and will plateau.",
   "quote":"if the returns on that investment start to go down, for example, that seems like a very scary thing",
   "entities":["GOOGL","MSFT","NVDA"]},
  {"type":"research_thread","headline":"Are GPU rental renewal prices really doubling?","stance":"neutral","confidence":0.8,"t_start":1370,
   "body":"Josh's second tripwire is rental rates: he says firms are afraid to re-sign long-term GPU rental deals because renewal prices are double the original, on contracts ranging from one to three years. He is watching for that trend to flatten or invert. The claim is specific and checkable.",
   "quote":"a lot of companies are terrified to resign their long-term GPU rental deals because the price that they're going to get them at this time around is going to be double",
   "research_question":"Pull published GPU rental and lease rates from CoreWeave, Nebius and Lambda for H100 and A100 over the last eight quarters. Are renewal prices actually 2x originals, and has the trend flattened?",
   "entities":["CoreWeave","Nebius","NVDA"]},
  {"type":"research_thread","headline":"The Broadcom TPU SPV is the working precedent for this structure","stance":"neutral","confidence":0.75,"t_start":692,
   "body":"Josh notes the structure has been run before at smaller scale: a special purpose vehicle buys TPUs and leases them, Broadcom provides residual value guarantees, Apollo and Blackstone supply the private credit. If the template exists and has performed, it is the best available basis for pricing the NVIDIA version.",
   "quote":"the Google Anthropic structure actually runs through this thing called an SPV a special purpose vehicle that buys TPUs and leases them with Broadcom providing the residual value guarantees",
   "research_question":"Find the disclosed terms of the Broadcom-backed TPU special purpose vehicle: who holds residual value risk, what the credit terms are, and how it has performed to date — then use it to price the NVIDIA structure.",
   "entities":["AVGO","GOOGL","Anthropic","Apollo","Blackstone"]},
  {"type":"research_thread","headline":"Pension funds are the ultimate holder of GPU residual risk","stance":"neutral","confidence":0.7,"t_start":578,
   "body":"Ejaaz identifies the main claimants behind the $500bn as pension funds — pools that historically avoid volatile assets — and asks on air whether this is reckless. He answers it with quotes from Solomon and Fink rather than with data on who has actually allocated.",
   "quote":"pension funds who have amassed a large amount of wealth and typically don't invest in high-volatile type assets... are the ones that are going to be backing a lot of this new GPU asset class",
   "research_question":"Which pension funds and insurers have disclosed allocations to AI-infrastructure private credit, at what size, and what residual value assumptions for GPUs sit inside those mandates?",
   "entities":["Goldman Sachs","BlackRock","Apollo"]},
]},

# ---------------------------------------------------------------------------
{
"meta": V("oln5SktcX9E", HAMISH, "Hamish Hodder",
          "The Biggest Trading Loss In History Just Happened", "2026-08-20T22:47:42+00:00"),
"summary": {
  "headline": "A 4x-levered single-theme book went from $45bn to under $10bn in a month",
  "summary": "Hamish reconstructs the collapse of Leopold Aschenbrenner's Situational Awareness: roughly $11.5bn of investor equity levered four times on the long side into AI-related names, plus a $14bn short book against software, controlling about $45bn gross. A cluster of July news hit every position at once, and a Goldman margin call on 29 July forced the entire ~$16bn public book into a single block trade to Citadel at more than a 10% discount. He is careful about causation — the stocks recovered because of the deal, not despite it — and raises the Citadel conflict question without asserting it.",
  "bullets": [
    "Structure: ~$11.5bn equity, ~$26bn long, ~$14bn short, ~$45bn gross, roughly 2x net exposure.",
    "July was down about 67% net per the investor letter — and the fund is still up ~80% for the year.",
    "The whole ~$16bn public book went to Citadel in one block trade at a discount of more than 10% to market.",
    "Intel's post-earnings reversal — up 8%, then down 16% the next morning — is read as the tell that a forced seller was in the market.",
    "Correlation, not stock picking, was the failure: every position was the same bet expressed different ways.",
  ],
  "so_what": "The transferable lesson is position sizing and correlation inside a single theme — and the names in that book are near the top of the 13F filings the other channel reviewed two days earlier.",
},
"items": [
  {"type":"macro_structure","headline":"Four-to-one leverage turns a 20% drawdown into a wipeout","stance":"neutral","confidence":0.9,"t_start":None,
   "body":"Hamish walks the arithmetic: $1 of equity plus $4 borrowed buys $5 of stock, so a 10% rise is a 50% return and a 10% fall costs half the equity. He notes the problem arrives well before the theoretical wipeout, because lenders enforce a maintenance level and force selling into a market with no buyers.",
   "quote":"for every $1 of the fund's equity, Liupold would borrow $3 to $4 to amplify the returns",
   "entities":["Situational Awareness","Goldman Sachs"]},
  {"type":"macro_structure","headline":"Diversification across 600 names inside one theme is still one bet","stance":"bearish","confidence":0.85,"t_start":None,
   "body":"Bloom Energy, SanDisk, CoreWeave, SK Hynix and Intel all fell together on a string of July news: Meta reselling surplus AI capacity, a Chinese model rivalling US frontier models, a report on Chinese lithography, and the Korean market falling 40% on retail margin debt. There was nowhere in the portfolio to hide.",
   "quote":"Pretty much all of the funds capital was invested in AI related stocks... and they basically all started to fall at the exact same time",
   "entities":["Bloom Energy","SNDK","CoreWeave","SK Hynix","INTC"]},
  {"type":"investable_idea","headline":"Strong earnings with a collapsing price is the forced-seller tell","stance":"neutral","confidence":0.8,"t_start":None,
   "body":"Intel beat on revenue and profit on 23 July, analysts revised forward estimates up, and the stock rose 8% — then gave it all back and fell another 8% the next morning. Hamish reads this as a distressed holder using good news as liquidity, and notes other firms inferred which fund it was by cross-referencing public filings.",
   "quote":"The most likely cause, a huge hedge fund is taking advantage of the good news to dump an enormous position, which they would only do if they were in serious trouble.",
   "entities":["INTC","Situational Awareness"]},
  {"type":"macro_structure","headline":"The block trade mechanic: distress is priced into the discount","stance":"contested","confidence":0.75,"t_start":None,
   "body":"Citadel takes the whole book at a discount to a market price that is already depressed precisely because the seller is in trouble. Hamish is emphatic that the subsequent recovery in those names was caused by the deal removing the overhang — without it, he argues, the fund would likely have gone to zero.",
   "quote":"Citadel is willing to do it because they get to acquire a huge chunk of shares at a discount to the current market price",
   "entities":["Citadel","Situational Awareness"]},
  {"type":"macro_structure","headline":"The Citadel conflict question is raised and explicitly not asserted","stance":"contested","confidence":0.5,"t_start":None,
   "body":"Citadel Securities published an interest-rate memo shortly before the deal that Hamish says probably hurt these stocks, while Citadel LLC bought the book below market. Both are Ken Griffin's, they are meant to operate independently, and they share a building. He states plainly there is no proof of anything illegal and leaves it to the viewer.",
   "quote":"Citadel Securities had released a memo about interest rates that almost certainly had a negative impact on the prices of these AI related stocks",
   "entities":["Citadel","Citadel Securities","Ken Griffin"]},
  {"type":"research_thread","headline":"Can forced selling be detected before the filings show it?","stance":"neutral","confidence":0.7,"t_start":None,
   "body":"Several firms worked out which fund was in trouble before it was public, by pairing an unexplained price move with holdings visible in 13Fs and annual reports. Hamish notes those filings carry a 45-day lag, which is what makes the price signal the leading one.",
   "quote":"The regulatory filings that let us see what stocks a hedge fund owns come with a 45day lag",
   "research_question":"Test whether 'strong earnings beat followed by a same-week price collapse' is a usable forced-seller signal: screen the last three years for the pattern and check each instance against subsequent 13F liquidations and reported fund distress.",
   "entities":["INTC","Situational Awareness"]},
  {"type":"research_thread","headline":"Rebuild the July 2026 sequence that broke the AI-hardware complex","stance":"neutral","confidence":0.7,"t_start":None,
   "body":"Four separate catalysts are named as compounding within weeks. Each is checkable, and together they are the template for how a crowded single-theme book unwinds — which matters because the same names dominate this quarter's institutional filings.",
   "quote":"Huge global companies down 10, 15, 20% in a handful of sessions. Some now down more than 50% since mid June.",
   "research_question":"Assemble the July 2026 timeline with primary sources: Meta reselling AI capacity, the Chinese frontier model release, the Chinese lithography report, and the Korean market drawdown. Which actually moved which names, and by how much?",
   "entities":["Meta","SK Hynix","KOSPI"]},
]},

# ---------------------------------------------------------------------------
{
"meta": V("qePg0Lh4-8I", HAMISH, "Hamish Hodder",
          "The Masa Son Collapse Is WILD", "2026-07-30T14:17:06+00:00",
          [(0, "Intro"), (128, "Euphoria"), (299, "Super Bowl XXXIV"), (546, "The Truth"),
           (862, "$75 Billion Meeting"), (1055, "Elon Musk?")]),
"summary": {
  "headline": "Masa Son's 99% drawdown is the base rate Hamish holds up against Musk's paper trillion",
  "summary": "SoftBank went from roughly $200bn of market cap to $2bn, and Son's own net worth from about $70bn to $600m, on a portfolio of ~600 internet companies across 130 countries that was in substance a single bet. Hamish fact-checks Son's 'richest man in the world' claim himself and finds at best a few-minute tie with Gates at ~$84bn. He then runs the comparison to Musk: Tesla at ~350x earnings against Toyota's ~10x, which on a premium-carmaker multiple would put Musk near $100bn — while conceding Tesla and SpaceX are real businesses and most dot-com holdings were not.",
  "bullets": [
    "SoftBank fell ~93% in 2000 and did not regain its February 2000 peak until February 2021 — 21 years underwater.",
    "600 companies in 130 countries was diversification in name only; every one was the same wager on the same thing.",
    "Hamish checked the 'richest man' claim hour by hour across two exchanges: at best a few-minute tie with Gates at ~$84bn.",
    "Tesla trades ~350x earnings against Toyota's ~10x; at 15-25x the business implies roughly $70-110bn, not $1.5tn.",
    "The recovery came from one six-minute meeting: $20m into Alibaba for ~30%, worth ~$75bn at the 2014 IPO.",
  ],
  "so_what": "This is a base-rate exercise rather than a call, and the useful output is the multiple sensitivity you can rerun yourself on any concentrated founder fortune.",
},
"items": [
  {"type":"macro_structure","headline":"Six hundred holdings, one bet — the diversification illusion","stance":"neutral","confidence":0.9,"t_start":546,
   "body":"Hamish's central point about SoftBank in 2000: stakes in ~600 companies across 130 countries looked like the opposite of concentration, but every one was an internet company and almost none made money. The count of positions said nothing about the count of underlying bets.",
   "quote":"what looked like an empire with investments in 600 different companies was really just one huge gamble",
   "entities":["SoftBank","Yahoo","Alibaba"]},
  {"type":"macro_structure","headline":"'New economy metrics' replaced earnings with eyeballs","stance":"contested","confidence":0.8,"t_start":546,
   "body":"He identifies the specific analytical failure: valuing companies on traffic rather than earnings, and then grossly overestimating what that traffic would convert into via advertising, subscriptions or product sales. The framing invites the reader to ask what today's equivalent metric is.",
   "quote":"you stopped valuing a company on its earnings and started valuing it on eyeballs",
   "entities":["SoftBank"]},
  {"type":"macro_structure","headline":"Twenty-one years to recover the peak","stance":"neutral","confidence":0.85,"t_start":546,
   "body":"The recovery number is the one that does the work in the argument: SoftBank shares did not see February 2000 again until February 2021. Web Van wiped ~60bn yen off the books in a year; Asia Global Crossing returned about six cents on the dollar.",
   "quote":"SoftBank shares didn't see their February 2000 peak again until February 2021, nearly 21 years underwater",
   "entities":["SoftBank","Webvan"]},
  {"type":"investable_idea","headline":"Tesla at ~350x earnings against Toyota at ~10x","stance":"bearish","confidence":0.7,"t_start":1055,
   "body":"Hamish runs the sensitivity rather than making a call: valued as a premium car maker at 15-25x earnings, Tesla implies roughly $70-110bn rather than $1.5tn. Applying a premium aerospace multiple to SpaceX and stripping AI optionality puts Musk near $100bn — an 85-90% fall — and he immediately notes both are legitimate businesses, unlike most of SoftBank's dot-com book.",
   "quote":"Tesla currently trades around 350 times its earnings while Toyota trades at around 10 times",
   "entities":["TSLA","Toyota","SpaceX"]},
  {"type":"research_thread","headline":"Son says today's boom is 50x bigger than the one that nearly bankrupted him","stance":"neutral","confidence":0.7,"t_start":1055,
   "body":"The man who lost 99% in the dot-com crash now describes the year 2000 peak as 'a small hill' next to the present cycle. Hamish plays the clip against his own comparison and lets the irony stand, without testing the claim on any measure.",
   "quote":"I think this is like more than 10x probably 50x bigger than",
   "research_question":"Compare dot-com capex and market capitalisation with 2025-26 AI capex on a common basis — share of GDP, share of total market cap, real dollars. Is Son's '50x bigger' claim defensible on any of them?",
   "entities":["SoftBank","NVDA"]},
  {"type":"research_thread","headline":"Rerun the Musk sensitivity with your own multiples","stance":"neutral","confidence":0.65,"t_start":1055,
   "body":"The whole comparison rests on one assumption — that the market eventually prices Tesla and SpaceX as the businesses they currently are rather than what they may become. That assumption is the thing to test, not accept.",
   "quote":"more than 80% of Musk's net worth is based on the promise of extraordinary growth achieved by his companies",
   "research_question":"Build the sensitivity yourself: Tesla at 15x, 25x and 40x forward earnings, SpaceX at premium-aerospace and at growth multiples. What net worth range results, and which single assumption dominates the answer?",
   "entities":["TSLA","SpaceX"]},
]},

# ---------------------------------------------------------------------------
{
"meta": V("nE_tu7vHc4w", HAMISH, "Hamish Hodder",
          "Investor Commits Fraud on Live TV", "2026-06-19T20:58:24+00:00"),
"summary": {
  "headline": "Andrew Left convicted on 13 counts — the line was half-truths, not a duty to disclose",
  "summary": "A federal jury convicted the Citron Research founder on 13 of 17 counts, including the securities-fraud-scheme charge carrying a 25-year maximum. The substantive part is the judge's instruction: a market commentator has no general duty to disclose positions, but a half-truth — affirmatively representing something material while omitting qualifying information — creates one. The DOJ documented a repeated architecture of build, set the exit order, then publish an extreme target, alongside more than $1m from Anson Advisers for advance notice routed through fabricated invoices, and hedge fund investor letters written for investors who did not exist.",
  "bullets": [
    "Convicted on 13 of 17 counts; the scheme charge carries a 25-year maximum, with sentencing set for August.",
    "The instruction that decided it: no general duty to disclose positions, but a half-truth creates one.",
    "XL Fleet shows the architecture — buy at ~$25 at 1:00pm, set a $27.50 sell at 1:37pm, tweet a $60 target at 1:41pm.",
    "Prosecution experts put the average Citron publication's market impact at ~12% across 15 companies and 5 years.",
    "More than $1m came from Anson Advisers for advance notice of publications, laundered through a third party on fake invoices.",
  ],
  "so_what": "If you trade on published short or long reports, the operative fact is that 'I'm still short' can be literally true with 61% already covered — that gap, not disclosure, is what the conviction turned on.",
},
"items": [
  {"type":"macro_structure","headline":"The half-truth doctrine is what actually decided the case","stance":"neutral","confidence":0.9,"t_start":None,
   "body":"The judge told the jury the law imposes no general duty on a market commentator to disclose positions or intentions — vindicating two years of defence argument — and then added that a statement which affirmatively represents something material while omitting critical qualifying information creates a duty to disclose. Saying you covered 'a small size' when you had covered 61% falls into that gap.",
   "quote":"When a statement is a halftruth, when it affirmatively represents something material while omitting critical qualifying information, a duty to disclose arises.",
   "entities":["SEC","DOJ","Citron Research"]},
  {"type":"macro_structure","headline":"The same architecture repeated across five years and fifteen companies","stance":"bearish","confidence":0.85,"t_start":None,
   "body":"Investigators found a pattern rather than a single trade: establish the position, place the limit order that exits it, then publish an extreme price target that guarantees media coverage and a sharp move. On XL Fleet the sell order was placed four minutes before the tweet; the trade returned $2.7m within a day.",
   "quote":"Builds a position in a stock, set an order to exit the position, all before he's told anyone about it.",
   "entities":["Citron Research","XL Fleet","NVDA","Twitter"]},
  {"type":"investable_idea","headline":"A Citron publication moved the average target ~12%","stance":"neutral","confidence":0.8,"t_start":None,
   "body":"The prosecution's experts quantified what the defendant had been boasting about privately: publications moved stocks by roughly 12% per report. Kronos fell almost 30% on the day of his short report; his Nvidia long tweet produced ~$930k in under two hours.",
   "quote":"Citron publications moved stocks by an average of about 12% per report",
   "entities":["Citron Research","Cronos Group","NVDA"]},
  {"type":"macro_structure","headline":"He was explicitly trading the algorithms, not the readers","stance":"neutral","confidence":0.8,"t_start":None,
   "body":"In private messages Left described his real mechanism: automated trading systems were programmed to react to Citron tweets, so publishing mid-day flipped them. He also told a podcast that stocks move so fast you have to trade first and read the report after — which the prosecution used to show he knew unsophisticated investors were acting on headlines alone.",
   "quote":"What I'm really doing is switching algorithms",
   "entities":["Citron Research","Facebook","Anson Advisers"]},
  {"type":"macro_structure","headline":"The verdict sheet carried a charge that had already been dropped","stance":"contested","confidence":0.6,"t_start":None,
   "body":"The jury unanimously ticked guilty on an eighteenth count — making false statements to federal investigators — that was dismissed before trial and never argued. The mistrial motion was rejected, with the judge saying she was confident the jury understood the issues and was not misled by a clerical error, but the point survives for appeal.",
   "quote":"a jury that would return a guilty verdict on a charge that no one explained to it speaks for itself about the reliability of this verdict",
   "entities":["Citron Research","DOJ"]},
  {"type":"research_thread","headline":"Where exactly is the line between lawful post-publication trading and fraud?","stance":"neutral","confidence":0.75,"t_start":None,
   "body":"Left's defence was that no law sets a holding period after you publish, and the judge partly agreed. The operative distinction is narrow and has real consequences for anyone publishing research while holding positions.",
   "quote":"the law does not impose a general duty to a market commentator to disclose their trading positions or intentions when they publish investment commentary",
   "research_question":"Read the actual jury instructions and the SEC complaint in the Andrew Left case. What precisely separates a lawful post-publication trade from the half-truth standard, and has any appellate court tested that line since?",
   "entities":["SEC","DOJ","Citron Research"]},
  {"type":"research_thread","headline":"Is the activist short-report price impact still there after the conviction?","stance":"neutral","confidence":0.7,"t_start":None,
   "body":"The ~12% average impact was measured over 2018-2023 on one publisher with an unusual reputation. Whether published short reports still move prices that much — and whether the conviction changed publisher behaviour — is directly measurable.",
   "quote":"Citron publications moved stocks by an average of about 12% per report",
   "research_question":"Take the 20 most recent activist short reports from major publishers and measure day-one and 30-day price impact. Is the ~12% Citron effect still present in the market, and has report frequency changed since the conviction?",
   "entities":["Citron Research","Hindenburg","Muddy Waters"]},
]},

# ---------------------------------------------------------------------------
{
"meta": V("FpJqQBwybfI", HAMISH, "Hamish Hodder",
          "The CS2 Market Collapse Is WILD", "2026-05-27T20:03:23+00:00"),
"summary": {
  "headline": "A 200-word patch note erased ~$3bn in two days and plausibly moved fees onto Valve's own rails",
  "summary": "Valve's 22 October 2025 Counter-Strike patch allowed five covert skins to be traded up into a knife or gloves, destroying overnight the scarcity that priced gold-tier items. The market fell from about $5.9bn to $3.5bn in a little over two days. Hamish's argument is structural: Steam caps single listings at $1,800, which pushes high-value trades onto third-party marketplaces where Valve earns nothing, and the patch shifts value down into ~$100 items that trade on Steam at a 15% commission. He also connects it to Valve's gambling exposure, including the New York Attorney General's suit filed four months later.",
  "bullets": [
    "The market went from ~$5.9bn to ~$3.5bn in just over two days; named collectors lost $550k, $40k and $50k+.",
    "The mechanism is the listing cap: Steam caps a single listing at $1,800, so the high end trades off-platform.",
    "Valve's two pipes: roughly $1bn a year in $2.50 keys, plus 15% of ~$1.3bn Steam marketplace volume (~$170m).",
    "One skin's six-month transaction value went from ~$45k to ~$600k — a more than tenfold increase in Valve's cut.",
    "The New York AG filed a 52-page suit on 25 February 2026 seeking disgorgement of all loot-box revenue.",
  ],
  "so_what": "No listed security here, and the honest answer is that this one is background — but the pattern of a platform owner repricing an entire market by changing one supply rule transfers directly to exchanges, app stores and marketplaces you may hold.",
},
"items": [
  {"type":"macro_structure","headline":"One supply rule, buried in a patch note, repriced a $6bn market","stance":"neutral","confidence":0.9,"t_start":None,
   "body":"Gold-tier items were previously obtainable only through a 0.26% loot-box roll. Letting five covert skins craft one instantly collapsed that scarcity: 20 million coverts existed against 5.5 million gold items. Steam transaction volume surged ~300% in hours while knife prices fell more than 60%.",
   "quote":"Players can now exchange five covert quality weapon skins for one gold tier item",
   "entities":["Valve","Counter-Strike","Steam"]},
  {"type":"macro_structure","headline":"The $1,800 listing cap is the whole mechanism","stance":"bullish","confidence":0.75,"t_start":None,
   "body":"Hamish's structural argument: high-value skins cannot be sold on Steam at all, so that trade — and its fees — goes to CS Float and Skinport. Pushing demand down into ~$100 coverts moves volume onto Valve's own marketplace at 15%. His AUG Chameleon example runs ~12,000 sales at $3-4 in one period against under 6,000 at ~$100 in the next.",
   "quote":"A $100 covert sits well under Steam's $1,800 listing cap. So, covert trades largely happen on the Steam marketplace, and Valve takes its 15% on every single one.",
   "entities":["Valve","Steam","Skinport","CS Float"]},
  {"type":"macro_structure","headline":"The patch also weakens the argument that loot boxes are gambling","stance":"contested","confidence":0.7,"t_start":None,
   "body":"Hamish traces a decade of regulatory pressure — Belgium and the Netherlands in 2018, France in 2019, the X-ray scanner loophole, the Genesis Terminal — and notes the New York AG sued four months after the patch. He is careful with the causal claim: 'plausibly related', and only Valve's legal team knows.",
   "quote":"the trade-up patch could plausibly be related to this lawsuit",
   "entities":["Valve","New York Attorney General"]},
  {"type":"investable_idea","headline":"A free game generates more than a billion dollars a year from cosmetics","stance":"neutral","confidence":0.65,"t_start":None,
   "body":"About 400 million cases are opened a year, each needing a $2.50 key paid to Valve, plus a 15% cut on roughly $1.3bn of Steam marketplace volume. Valve is private, so none of this is investable directly, but it sizes the digital-goods economy that public marketplaces are competing for.",
   "quote":"just on keys, that's roughly a billion dollars a year from one free game spent on purely cosmetic items",
   "entities":["Valve","Steam"]},
  {"type":"research_thread","headline":"Read the New York AG complaint and size the disgorgement exposure","stance":"neutral","confidence":0.75,"t_start":None,
   "body":"The relief sought is a permanent injunction plus disgorgement of every dollar Valve has made from loot boxes, plus fines. If that succeeds it is a template for other state attorneys general and for every publisher running the same model.",
   "quote":"the New York Attorney General filed a 52-page lawsuit against Valve Corporation",
   "research_question":"Read the New York AG's complaint against Valve: what relief is sought, what is the plausible disgorgement exposure, which other state AGs have followed, and which listed publishers run comparable loot-box mechanics?",
   "entities":["Valve","New York Attorney General","EA","Take-Two"]},
  {"type":"research_thread","headline":"Test the fee-capture claim — Hamish says himself he cannot prove it","stance":"neutral","confidence":0.7,"t_start":None,
   "body":"The commission-shift thesis rests on one worked example and an explicit admission that Valve's internal numbers are not visible. Steam marketplace data is public enough to test the claim across a basket rather than a single skin.",
   "quote":"we can't see Valve's internal commission numbers, so we can't prove whether this has been a profitable decision overall",
   "research_question":"Pull Steam marketplace volume and median prices for a basket of 30 covert skins for six months before and after 22 October 2025, and estimate the change in Valve's 15% commission take. Does the fee-capture thesis hold beyond the AUG Chameleon?",
   "entities":["Valve","Steam"]},
]},
]


def main() -> None:
    cfg = Config.load()
    store = Store(cfg.db_path)

    for ep in EPISODES:
        meta = ep["meta"]
        store.upsert_video(meta)
        store.set_transcript(meta.video_id, "(seeded from an out-of-band transcript fetch)", 0)
        n = store.save_analysis(meta.video_id, ep["summary"], ep["items"])
        print(f"{meta.video_id}  {n:>2d} signals  {meta.title[:52]}")

    rows = store.dashboard_rows(cfg.dashboard.max_videos, cfg.analyze.min_confidence)

    full = ROOT / cfg.dashboard.output_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(render(rows, cfg.dashboard, sources=cfg.channels))

    frag = full.with_name("dashboard_artifact.html")
    frag.write_text(render(rows, cfg.dashboard, fragment=True, sources=cfg.channels))

    (full.with_suffix(".json")).write_text(render_json(rows))

    print(f"\n{len(rows)} episodes, {sum(len(r['items']) for r in rows)} signals")
    print(f"dashboard -> {full}")
    print(f"artifact  -> {frag}")
    store.close()


if __name__ == "__main__":
    main()
