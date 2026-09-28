Most cost alerts don't have a detection problem. They have amnesia.

Ours paged someone about the same "$2k GPU spike" on the 1st of every month. Someone explained it in Slack every month.

So I built SpendSight: z-scores find the spike, Hindsight agent memory decides what it means.

What worked:
→ Stats detect, memory interprets.
→ "Expected" needs a cited past event, enforced in code.
→ Model recalls the usual size; code does the maths.
→ One memory doc per incident, replaced on correction.
→ Retain deploy logs. It tied an S3 egress bug to deploy #421.

6-month replay:
Correct verdicts: 1/13 → 9/13
False alarms: 9 → 4, all first sightings
Missed waste: 0

Repo: https://github.com/Srihitha-abc/spendsight

#AIAgents #Hindsight #AgentMemory #LLM
