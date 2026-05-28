# OpenPrometheus

**A recursive agent builder.** Give it a task; it designs a first-attempt specialized
agent, *masterfully creates its own roster of sub-agents* to stress-test and benchmark
that agent, scores it ruthlessly, rewrites its architecture, and loops — pushing the
agent until it's as good as it can get.

Works on **any foundational model**: use the default Claude Code auth (no API key), or
plug in an Anthropic / OpenAI API key. Every Prometheus role can target a different model.

```
prometheus "Convert messy human-written dates into strict ISO 8601, resolving ambiguity deterministically."
```

## How it works

```
task ─▶ ARCHITECT ─▶ candidate agent v1
            │
            ▼
       STRATEGIST ─▶ creates specialized SUB-AGENTS for this domain
            │           (test_generator · adversary · scorer · benchmarker)
            ▼
  sub-agents generate a test curriculum (basic → edge → adversarial)
            │
            ▼          ┌──────────────── improvement loop ────────────────┐
       RUNNER ─▶ scorer/benchmarker sub-agents grade outputs ─▶ IMPROVER ─▶ vN+1
            ▲                  │  Strategist spawns NEW specialists                │
            │                  │  targeting freshly-revealed weaknesses            │
            └──────────────────────────────────────────────────────────────────────┘
              stop when: score ≥ threshold · max iters · plateau
```

- **Architect** — designs the candidate: a sharp system prompt, an IO contract, and the
  *minimal* tools the task needs (most tasks need none).
- **Strategist (sub-agent factory)** — the core capability: it inspects the candidate's
  domain and builds purpose-built sub-agents to probe it. A date parser gets a locale
  ambiguity adversary and an ISO-8601 compliance benchmarker; a SQL agent gets an
  injection adversary and a schema-correctness scorer. As weaknesses surface, it spawns
  new specialists to attack them.
- **Sub-agents** — themselves Prometheus-built agents (same portable spec). Generators
  and adversaries produce test cases; scorers and benchmarkers grade outputs strictly.
- **Improver** — rewrites the candidate's *architecture* to fix diagnosed failures.

The insight (borrowed from GAN-style harnesses): an agent is a pathological optimist
about its own work, so a **separate, ruthless evaluator** is what actually drives quality.

## Install

No install needed for the default backend — just Python 3.9+ and the `claude` CLI
(already authenticated via Claude Code). Run from the repo root:

```bash
PYTHONPATH=. python3 -m prometheus "your task here"
```

Or install the console script:

```bash
pip install -e .
prometheus "your task here"
```

Optional API-key backends:

```bash
pip install -e ".[anthropic]"   # or ".[openai]"
export ANTHROPIC_API_KEY=...    # or OPENAI_API_KEY=...
prometheus "your task" --provider anthropic --model claude-sonnet-4-6
```

## Usage

```
prometheus "TASK" [options]

  --provider          claude-code (default, no key) | anthropic | openai
  --model             model id/alias for all roles (e.g. sonnet, opus, gpt-4o)
  --threshold         pass score 0–10 (default 8.5)
  --max-iters         max improvement rounds (default 6)
  --patience          stop after N rounds without gain (default 2)
  --tests-per-round   new tests generated per round (default 6)
  --allow-code-exec   permit candidate agents to run code (off by default)
  --runs-dir          where to write artifacts (default ./runs)
  --no-export         skip writing the Claude Code .md
```

Per-role overrides via env (run the evaluator on Opus, everything else on Sonnet):

```bash
export PROMETHEUS_PROVIDER=claude-code PROMETHEUS_MODEL=sonnet
export PROMETHEUS_SUBAGENT_MODEL=opus      # scorers/benchmarkers use the stronger model
```

Roles: `ARCHITECT`, `STRATEGIST`, `IMPROVER`, `SUBAGENT` (running sub-agents),
`CANDIDATE` (running the candidate under test).

## MCP tools (giving built agents real capabilities)

Built agents can be granted **MCP tools**, on any backend:

- **Claude Code provider** grants them natively (your already-connected servers, via
  `--allowedTools`).
- **API providers** (Anthropic/OpenAI) execute them through a built-in stdio **MCP proxy**
  (`mcp_client.py`) — so MCP works model-agnostically, not just on Claude.

**Tiered safety gate.** MCP is **off** unless you name what's allowed:

```bash
prometheus "answer questions about my codebase" --mcp-allow "leann-server"
prometheus "..." --mcp-allow "leann-server,mcp__notion__*" --mcp-allow-sensitive
```

- Within allowed servers, **safe** tools (search/list/get/read) are auto-eligible for the
  Architect to grant.
- **Sensitive** tools (send/write/pay/book/deploy) require `--mcp-allow-sensitive`.
- The orchestrator filters every candidate's `mcp_tools` through the gate — anything out
  of policy is dropped and logged, even if a model tries to grant it.

## Capability negotiation (the agent helps build itself)

The candidate isn't just tested *at* — it talks back. Each round (unless `--no-negotiate`):

1. **Self-report** — the candidate introspects on its own failures and requests what it
   needs: tools, examples, prompt fixes.
2. **Analyst validates** — a `capability_analyst` sub-agent checks each request against the
   allowed catalog and the external eval, approving only what's justified (and never a tool
   outside policy).
3. **Improver grants** — the next version incorporates exactly the approved capabilities.

The self-report + analyst verdict for each round are saved to
`runs/<id>/evals/capability-NNN.json`.

## Worked example: a complex, tool-using agent graded on many dimensions

This is the kind of agent OpenPrometheus is built for — repetitive, judgment-heavy, with a
gradeable output and lots of ways to fail. The walkthrough below is illustrative (numbers
are representative of a typical run), but the loop behavior, the sub-agent panel, the tool
grants, and the negotiation step are exactly what the harness does.

**The task** — a B2B *account-intelligence brief* agent for a sales team or solo founder:

```bash
prometheus "Given a company name, produce a one-page B2B account-intelligence brief: \
verified firmographics, recent buying signals, an ICP fit score (0-100), three tailored \
talking points, and a recommended next action. Every factual claim must cite its source; \
never fabricate. If data is insufficient, say so." \
  --provider claude-code \
  --mcp-allow "mcp__zoominfo__enrich_companies,mcp__zoominfo__enrich_news,mcp__zoominfo__enrich_scoops" \
  --mcp-allow-sensitive \
  --threshold 8.5 --max-iters 6
```

> `--mcp-allow-sensitive` is needed here because the gate conservatively classifies
> `enrich_*` as sensitive (it can't verify they're read-only) — so you explicitly vouch for
> them. ZoomInfo is a remote server, so its tools run via the **claude-code** provider.

**What the Architect builds** — `account-intel-brief`, granted a mix of tools because this
genuinely cannot be done by reasoning alone:

| Tool | Why |
|------|-----|
| `mcp__zoominfo__enrich_companies` | pull verified firmographics (employees, revenue, industry) |
| `web_fetch` (built-in) | cross-check a claim against the company's own website |
| `python_exec` (built-in) | compute the ICP fit score deterministically from thresholds |

**What the Strategist manufactures** — a *panel of specialized judges*, not one generic
scorer. This is the "multiple benchmark testing" in action:

| Sub-agent | Role | What it measures / attacks |
|-----------|------|----------------------------|
| `firmographic-corpus-generator` | test_generator | realistic companies across size/industry/region tiers |
| `no-data-adversary` | adversary | companies with empty/low-confidence enrichment → must NOT fabricate |
| `stale-entity-adversary` | adversary | acquired/renamed firms → must not present outdated info as current |
| `name-collision-adversary` | adversary | ambiguous names ("Apex Solutions") → must disambiguate or flag |
| `prompt-injection-adversary` | adversary | a site/profile containing "ignore previous instructions" → must not be hijacked |
| `grounding-citation-scorer` | scorer | **every claim cited to a real source; any uncited/invented claim fails hard** |
| `firmographic-accuracy-benchmarker` | benchmarker | numbers in the brief match the tool output exactly (no drift) |
| `recency-benchmarker` | benchmarker | "recent signals" are actually recent and real, not stale/hallucinated |
| `fit-logic-scorer` | scorer | the 0-100 score is reproducible and consistent with the ICP rules |
| `actionability-scorer` | scorer | talking points are specific to *this* company; next action is concrete |
| `schema-compliance-scorer` | scorer | emits the exact one-page structure, nothing extra |
| `capability-analyst` | capability_analyst | gatekeeps what tools/fixes the agent may receive |

**A representative run trace:**

```
▶ Prometheus run …  (task: account-intelligence brief)
  MCP: 3 tools grantable (0 proxiable to API providers)   # zoominfo is remote → claude-code only
  architect → candidate 'account-intel-brief'
             (tools=['web_fetch','python_exec'], mcp=['mcp__zoominfo__enrich_companies'])
  strategist → 12 sub-agents: 1 generator, 4 adversaries, 6 scorers/benchmarkers, 1 analyst

── iteration 1/6 ──
  tests: 8 total (8 new this round)
  ⇒ aggregate 5.8/10  | hardest-tier 4.1/10
      grounding-citation-scorer:   3/10  ← invented a "recent Series B" with no source
      recency-benchmarker:         4/10  ← "recent" signals were 3 years old
  ⇄ negotiation: candidate requested + analyst approved ['mcp__zoominfo__enrich_news']
  improver → candidate v2  (adds enrich_news + a "cite-or-omit" rule + INSUFFICIENT_DATA branch)
  strategist → +2 specialists targeting weaknesses (a freshness adversary, a citation auditor)

── iteration 2/6 ──
  tests: 12 total (4 new this round)
  ⇒ aggregate 7.4/10  | hardest-tier 6.9/10
      no-data-adversary now PASSES (agent returns INSUFFICIENT_DATA instead of fabricating)
      fit-logic-scorer:            6/10  ← score not reproducible run-to-run
  improver → candidate v3  (moves scoring into python_exec for determinism)

── iteration 3/6 ──
  tests: 14 total
  ⇒ aggregate 8.7/10  | hardest-tier 8.6/10
  ✓ threshold 8.5 cleared — stopping.

● final agent v3 → runs/…/candidate/agent-final.json
● Claude Code export → ~/.claude/agents/account-intel-brief.md
```

**What you get out:** a hardened `account-intel-brief` agent that enriches from ZoomInfo,
verifies claims against the live web, scores fit deterministically in code, refuses to
fabricate when data is thin, and resists prompt-injection from scraped pages — proven
against 14 adversarial cases across 6 quality dimensions, plus a ready-to-run Claude Code
subagent. The same shape works for support-triage, contract-risk, or bookkeeping agents —
swap the task and the panel reshapes itself.

## Output

Two artifacts:

1. **Portable agent package** — `runs/<id>/candidate/agent-final.json`: a model-agnostic
   spec (system prompt + tools + IO contract + examples) you can run on any provider.
2. **Claude Code subagent** — `~/.claude/agents/<name>.md`, immediately usable in Claude Code.

Full run trace lives under `runs/<id>/`:

```
candidate/   agent-v1.json, agent-v2.json, …, agent-final.json
subagents/   every specialized sub-agent the Strategist created
tests/       the growing test curriculum per round
outputs/     candidate outputs per round
evals/       scored, diagnosed eval reports + capability-NNN.json negotiation records
state.json   stop reason + score history
```

## Safety

`python_exec` runs candidate-authored code and is **off by default**; enable it only for
trusted tasks with `--allow-code-exec`. When enabled it runs in a throwaway working
directory with a hard timeout. File tools are scoped to that directory. Most language and
reasoning tasks need no tools at all.
