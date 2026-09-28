# SpendSight: 3-minute demo script

My name: [YOUR NAME]

Before recording: backend on :8000 and frontend on :5173 (`./run.ps1` or `./run.sh`), `.env` filled in so the header badges show `memory: hindsight-cloud` and `llm: groq:...`. Browser zoomed to 110–125%, notifications off.

---

## 1. Intro (0:00–0:30)

**On screen:** webcam or the dashboard header at http://localhost:5173.

> "Hi, I'm [YOUR NAME]. This is SpendSight, a cloud cost anomaly agent with a memory. It spots spikes in an AWS bill, and then, instead of paging you, it remembers what the last similar spike turned out to be. The memory is Hindsight. Every time an engineer answers an alert, that answer goes back into memory, and the agent gets quieter."

## 2. The problem (0:30–1:00)

**On screen:** click **Reset memory**, then **Next month ▸** once (April), then once more (May). Toggle the feed to **Memory OFF**.

> "Here's the problem. On the 1st of every month our ML team retrains a model on eight p4d GPUs, about $2,200 overnight. The detector sees it, which is fine. But without memory, look at the verdict: 'Needs review. No history, can't tell if this is planned.' It says that in May, June, July, every month. Someone explains it in Slack every time, and the tool forgets."

**Point at:** the May card's "Without memory" panel.

## 3. Live demo (1:00–2:30)

**On screen:** switch to **Side by side**. On the May card, point out the "cold start: no history yet" chip on the memory side.

> "With memory on, May is honest too: it's a cold start. Then the engineers reply." (Point at the feedback thread: Daniel asks, Priya says it's the monthly retrain.) "Those replies get retained into Hindsight, one memory document per incident."

**On screen:** **Next month ▸** (June). Open the June 1 card and expand "memories recalled from Hindsight".

> "June. Same spike, but now recall finds May's thread, and the verdict is Expected, citing that event. That citation is required: there's a rule, enforced in code, that it can never say Expected without citing a past event."

**On screen:** **Run all**. While it runs, show the log in the replay panel. Then open the July 22 S3 card.

> "July has a real bug. image-service deploy #421 started serving thumbnails straight from S3. Because the deploy log is in memory too, the agent links the egress spike to that deploy and suggests the person who shipped it."

**On screen:** September cards: the 1st (Expected but unusual), then the 17th (New waste). Click one to zoom the cost chart.

> "September is my favourite. The retrain costs 2.3 times the usual, and the agent flags it as expected but unusual and cites the August deploy that grew the training set. On the 17th, a forgotten p3 GPU shows up in the exact same team and service as the retrain, and memory doesn't wave it through: wrong day, wrong instance type, no precedent. New waste."

**On screen:** scroll to **False alarms per month**, then **What the agent has learned**.

> "Here's the learning curve: false alarms without memory versus with it. And this is the Hindsight mental model, 'Known recurring cost patterns', which is the agent's current beliefs in plain English."

**Optional (if time):** type a correction on the load-test card ("not waste, planned load test") and click **Expected**. Say: "Corrections replace the old memory instead of competing with it."

## 4. Takeaway (2:30–3:00)

**On screen:** webcam, or the side-by-side card.

> "What surprised me: memory makes an agent more confident, and that's dangerous if you're not careful. The design work was deciding where it's allowed to be confident. Statistics detect, memory interprets, and 'expected' always needs evidence. The code's in the repo, and Hindsight is linked below."

---

## Video titles

1. My Cost Alerts Paged Us Every Month. So I Gave Them Memory.
2. Memory ON vs OFF: Same AWS Spike, Different Verdict
3. This Agent Caught a Forgotten GPU Hiding Behind Our ML Retrain
4. Building a FinOps Agent That Learns From Slack Replies (Hindsight Demo)
5. 6 Months of AWS Bills, Replayed: Watch an Agent Learn

## Thumbnail prompt (Nano Banana)

Generate a viral thumbnail for this YouTube video. Make the thumbnail attention grabbing and something that people scrolling would want to click on if they see it. The aspect ratio needs to be 16:9.

Here is the video script: [paste the script above]. Visual idea: the attached person on the right looking surprised; on the left, a spiking cloud-cost line chart with a red dot; two stacked labels, "MEMORY OFF: ⚠ PAGE" in orange and "MEMORY ON: ✓ EXPECTED" in blue; big bold text "IT REMEMBERED."
