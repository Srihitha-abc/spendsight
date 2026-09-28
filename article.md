# Our Cost Alerts Had Amnesia Until Hindsight Remembered Why

On the first of every month, our cost alerting flagged a $2,200 overnight GPU spike in the ML account. And every month, someone from the ML team explained in Slack that it was the scheduled model retrain. The detector was never wrong about the spike. It just had no idea it had seen the same thing, with the same explanation, thirty days earlier.

So we built SpendSight, a cloud cost agent whose main job is to remember past anomalies, what engineers said about them, and what got deployed around them. The memory layer is [Hindsight, an open-source agent memory system](https://github.com/vectorize-io/hindsight). This post covers the design decisions that mattered, including two where our first version was wrong. The code is on [GitHub](https://github.com/Srihitha-abc/spendsight).

## What it does

SpendSight ingests daily billing data: AWS Cost & Usage Reports, Cost Explorer exports, GCP, Azure or FOCUS files. For each (account, service, team) group, it compares each day with the previous 14. A day is flagged when its z-score is above 3 *and* it's at least $150 over baseline. Consecutive flagged days merge into one incident, and an incident keeps extending while cost stays above its starting baseline. That way a forgotten instance shows up as one 13-day, $4,000 incident rather than a single blip that disappears once the rolling window absorbs it.

None of that involves an LLM. That was our first real decision, and we'd make it again: **statistics decide *that* something happened; memory decides *what it means*.** A rolling z-score is cheap, deterministic and testable, and keeping the LLM out of detection is what lets us measure whether memory changed an outcome.

Every incident then goes through three steps:

1. **Recall.** We describe the spike in plain language (service, team, weekday, day of month, dollar delta, top line items) and ask Hindsight for similar past events, engineer feedback and nearby deploys.
2. **Reflect.** Hindsight's `reflect` produces a structured verdict: `EXPECTED`, `NEW_WASTE`, or `EXPECTED_BUT_UNUSUAL` (a known event that came in much larger than usual). Each verdict comes with cited past events, a suggested owner and the dollar impact.
3. **Retain.** When an engineer answers (Expected, Waste, Investigating, plus a note), the whole thread goes back into memory, along with the deploy log.

[Screenshot: dashboard with verdict cards and cited past events]

## Setting up the memory bank

Each workspace (one company, one billing source) gets its own Hindsight memory bank, so one customer's retraining schedule never explains another customer's spike. The bank carries a mission, a disposition and a set of directives, and [the Hindsight docs](https://hindsight.vectorize.io/) describe how `reflect` reasons with all three:

```python
c.create_bank(bank, name=f"{self.company} FinOps", mission=mission_for(self.company), retain_mission=RETAIN_MISSION,
              disposition_skepticism=5, disposition_literalism=3, disposition_empathy=2)
```

The directives are short and blunt:

- Never mark a spike as EXPECTED without citing at least one matching past event.
- Always state the dollar impact.
- If a known recurring event is much larger than its usual range, say EXPECTED_BUT_UNUSUAL.
- A deploy that provisions resources explains why they exist, not how long they may run.

We set skepticism to the maximum on purpose. Memory makes an agent *more* confident, and in cost monitoring the dangerous label isn't "waste". It's "expected", because "expected" suppresses a page.

## The core problem: memory can launder waste

The case that shaped the design is a forgotten GPU. On September 17th, someone launched a `p3.8xlarge` from the console for an experiment and never terminated it. It's tagged `ml-research` and it's EC2 in the ML account, so it lands in exactly the same cost group as the monthly retrain. A model that sees "ml-research EC2 spike" next to five memories saying "ml-research EC2 spikes are the retrain, please stop paging us" will happily call it expected.

That's worse than having no memory at all. So the verdict is built from specifics, and anything that can be enforced in code, is. Recall is anchored in time, so a replay or backtest never sees feedback from the future:

```python
resp = self.client.recall(self.bank_id, query, max_tokens=max_tokens, budget="mid",
                          query_timestamp=as_of.isoformat() if as_of else None)
```

The verdict comes from `reflect` with a JSON schema, so we get a parseable object instead of prose. Every output is validated with Pydantic; if it's invalid, we retry once, then fall back to `NEEDS_REVIEW`:

```python
resp = self.client.reflect(self.bank_id, query, budget="mid", context=context,
                           response_schema=response_schema, include_facts=True, apply_all_directives=True)
```

With specific memories in context (the retrain is always on the 1st, always eight `p4d.24xlarge` instances, always about $2,200), the forgotten box came back `NEW_WASTE`:

> "driven by a single p3.8xlarge… far smaller than the recurring monthly retrain spikes ($2k–$5k)… No prior ml-research event shows this resource mix or magnitude, suggesting idle or forgotten resources."

## Two things the model got wrong, and what we moved into code

Our first real run against Hindsight produced two failures. Both were reasoning errors, not bugs.

**A deploy is an explanation, not an excuse.** A staging performance environment was left running over a weekend. Reflect cited the deploy that created it ("16x m6i.4xlarge for load profiling") and called the spend expected. That satisfied our citation rule and was exactly the wrong answer. July's load test was different: its deploy said "auto-teardown 20:00 UTC". So we added the fourth directive and enforced it in code: if a deploy is the only evidence, it must state a schedule or teardown, or the verdict drops to `NEEDS_REVIEW`.

**The model recalls; the code does the maths.** After that fix, perfectly normal retrains started coming back `EXPECTED_BUT_UNUSUAL`. The explanation even said "1.03x the usual excess of $2,165". The arithmetic was right and the label was wrong: the model kept anchoring on "15x the daily baseline", which is true of every retrain. So the schema now asks for one extra number, the usual excess of *past occurrences*, and code picks the label:

```python
r = anomaly.delta_usd / v.usual_excess_usd
label = VerdictLabel.EXPECTED_BUT_UNUSUAL if r >= UNUSUAL_RATIO else VerdictLabel.EXPECTED
```

We also started putting calendar facts in the prompt ("2 days before the end of Q3") after the agent decided a September 29th spike was "not tied to quarter-end reporting".

## Corrections replace, they don't compete

Feedback changes. In July, someone marked 60 new prod instances as waste; thirty-five minutes later, the owner replied that it was the planned load test. Stored as two memories, those are contradictory facts about one event, and which one wins comes down to ranking.

Instead, each anomaly gets one memory document that holds the whole feedback thread plus the final resolution, and every new response replaces it:

```python
# update_mode="replace": a corrected verdict replaces the stale memory for the same anomaly.
self.client.retain(self.bank_id, content, timestamp=timestamp, context=context, document_id=document_id,
                   tags=tags, metadata=metadata, update_mode="replace")
```

The history of the disagreement survives, but no stale "waste" fact floats around on its own. The same mechanism powers the "correct this verdict" box on every alert card.

## Making memory visible

Readers of a verdict need to see *why*, so the UI is built around the memory:

- **Cited past events** on every card, plus an expandable list of what Hindsight recalled.
- **Memory ON / OFF**: the same anomaly analysed twice, with the same model and the same guardrails, one with history and one without.
- **Replay**: step through six months, applying engineer feedback as it happened, and watch the alerts drop.
- **What the agent has learned**: a Hindsight mental model called "Known recurring cost patterns", rebuilt from memory. Ours became a table of recurring events, usual dollar ranges and owners.

[Screenshot: memory ON vs OFF, side by side]
[Screenshot: "What the agent has learned" mental model]

## Before and after: the same spike, six months apart

**May, empty memory.** Eight `p4d.24xlarge` instances appear overnight: +$2,165/day. Nobody has classified anything like it, so the agent doesn't guess: *"Unknown: no history yet, please classify."* FinOps asks, the ML lead answers, and the thread is retained.

**June to August.** Same spike, now `EXPECTED`, citing May's thread.

**September.** The retrain costs +$5,146/day. Memory off, the agent can only see a very large spike and flags it for a human. Memory on, it says `EXPECTED_BUT_UNUSUAL`:

> "its excess ($5,146) is more than double the typical excess of ~$2,270 observed in May–August."

Across the six-month replay, the memory-off agent raised an alert on every expected spike, every month, and never improved. With memory, false alarms on expected spikes fell from nine to four, and all four were first sightings. None of the planted waste slipped through.

[Screenshot: false alarms per month, with vs without memory]

## Lessons we'd reuse

1. **Split detection from interpretation.** Statistics for "something happened"; memory plus an LLM for "what it means".
2. **Make positive verdicts earn their evidence.** "Expected" silences a page. Require a citation, and enforce it in code as well as in a directive.
3. **Ask the model for facts, not arithmetic.** Have it report what it recalled (the usual size of an event) and compute ratios and thresholds yourself.
4. **One memory document per event, replaced on correction.** It handles conflicting feedback without leaving stale facts behind.
5. **The honest limitation:** recall is only half the job. The deploy that explains September's 2.3x retrain, which added 18 months of GPS history to the training set, *was* in memory. The agent flagged the size correctly but didn't connect the cause. The engineer's note is what taught the bank. Memory gives you the raw material; the reasoning over it still needs guardrails and people.

If you're thinking about adding memory to an agent, Vectorize's primer on [what agent memory is and why it matters](https://vectorize.io/what-is-agent-memory) is a good place to start. What surprised us wasn't how much memory helped. It was how much of the work was deciding where the agent is allowed to be confident.
