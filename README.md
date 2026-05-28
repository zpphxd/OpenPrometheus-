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
