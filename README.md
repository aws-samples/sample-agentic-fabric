# Agentic Fabric: Arbiter

A constitutional coordination substrate for autonomous agent systems on AWS. This is the reference implementation for the [Architecting Autonomy](https://aaronsempf.substack.com) article series, demonstrating how multi-agent systems can be governed through explicit authority design rather than compensatory oversight.

## What This Is

The Arbiter implements a distributed, event-driven multi-agent architecture where:

- **Agents are dynamically generated** — a Fabricator agent creates new worker agents on demand when the system encounters tasks no existing agent can handle
- **Every dispatch is governed** — a deterministic control-surface band evaluates authority scopes and composition contracts before any agent executes, producing legibility records as a structural byproduct
- **The system learns from its own governance** — escalated conflicts are resolved by humans, encoded as case law, and handled deterministically on recurrence

The architecture evolves through three coordination patterns — Agent Broker, Supervisor, and Arbiter — each building on the previous. The governance layer implements the authority model from the Architecting Autonomy series: authority as a first-class primitive with scope, enforcement, delegation, and termination properties.

## Architecture

```
                        ┌─────────────────┐
                        │  Neural Weave   │
                        │  (EventBridge)  │
                        └────┬───┬───┬────┘
                             │   │   │
                    ┌────────┘   │   └─────────┐
                    │            │             │
              ┌─────▼──────┐ ┌───▼───┐ ┌───────▼───────┐
              │  Arbiter   │ │Workers│ │  Fabricator   │
              │(Supervisor)│ │       │ │               │
              └─────┬──────┘ └───┬───┘ └───────────────┘
                    │            │
              ┌─────▼────────────▼──────────────────────┐
              │       Control-Surface Band              │
              │  ┌───────────┐  ┌────────────────────┐  │
              │  │ Authority │  │    Composition     │  │
              │  │  Scopes   │  │    Contracts       │  │
              │  └───────────┘  └────────────────────┘  │
              │  ┌───────────┐  ┌────────────────────┐  │
              │  │Arbitration│  │    Legibility      │  │
              │  │ Patterns  │  │    Ledger          │  │
              │  └───────────┘  └────────────────────┘  │
              └─────────────────────────────────────────┘
                    │
              ┌─────▼───────────────────────────────────┐
              │          Fabric Memory                  │
              │  Agent Metrics │ Workflow Outcomes      │
              └─────────────────────────────────────────┘
```

**Arbiter** — Receives task events via EventBridge, uses Amazon Bedrock Converse API to select agents (agent-as-tool pattern), dispatches work asynchronously via SQS, tracks fan-out/fan-in completion, and loops until done. Falls back to the Fabricator when no agent matches.

**Governance Engine** — Deterministic Python evaluation layer (no LLM) deployed as a Lambda Layer. Evaluates every dispatch against authority scopes, composition contracts, and the constitutional hierarchy. Implements four arbitration patterns: scope-based, priority, conjunction, and state-aware with monotonic reduction. All permits must survive mandatory constitutional review — the constitution is always evaluated and never bypassed by more specific contracts. Produces a write-once legibility record for every evaluation.

**Fabric Memory** — Tracks agent metrics (invocation counts, deny rates, failure rates) and workflow outcomes across all workflows. Populates the governance dispatch context so scope conditions and limits evaluate against real operational data. Enriches the Arbiter's system prompt with agent health summaries.

**Workers** — Dynamically generated agents executed by a generic wrapper that downloads code from S3, injects a governance tool handler, and calls the agent's `handler()` function. The tool handler evaluates every Strands SDK tool call against a denied-tools list.

**Fabricator** — Generates new agent code using the Strands Agents SDK, uploads to S3, registers in the Fabric Index, and publishes a completion event so the Arbiter can retry with the new capability.

## Architecting Autonomy Series Mapping

This implementation provides concrete code for the architectural concepts described in the series:

| Series Article | Implementation |
|---|---|
| [Article 8: The Unit of Authority](https://aaronsempf.substack.com) | `AuthorityUnit` + `AuthorityScope` in [`src/governance/models.py`](src/governance/models.py) |
| [Article 9: Authority Composition](https://aaronsempf.substack.com) | `CompositionContract` with four primitives in [`src/governance/models.py`](src/governance/models.py) |
| [Article 11: Governance at Machine Speed](https://aaronsempf.substack.com) | `governed_process_agent_call()` control-surface band in [`src/supervisor/index.py`](src/supervisor/index.py) |
| Companion: The Arbitration Patterns | `GovernanceEngine.evaluate()` in [`src/governance/engine.py`](src/governance/engine.py) |
| Companion: Authority Graph Formalization | Data model in [`src/governance/models.py`](src/governance/models.py) |

## Project Structure

```
Arbiter/
├── src/
│   ├── supervisor/          # Arbiter orchestration (Bedrock Converse API)
│   │   ├── index.py         #   Lambda handler, governed dispatch, fabrication fallback
│   │   ├── agent_config.py  #   Fabric Index: GSI-backed agent config with caching
│   │   └── memory.py        #   Fabric Memory: context builder, metrics, outcomes
│   │
│   ├── governance/          # Deterministic governance engine (Lambda Layer)
│   │   ├── models.py        #   Authority model: scopes, units, contracts, findings
│   │   ├── engine.py        #   Four arbitration patterns + constitutional review
│   │   ├── hierarchy.py     #   Constitutional hierarchy loader (DynamoDB, 4 tables)
│   │   ├── ledger.py        #   Write-once legibility record writer
│   │   └── case_law_admin.py#   CLI for encoding/revoking case law entries
│   │
│   ├── fabricator/          # Agent/tool code generation (Strands SDK)
│   │   ├── index.py         #   Fabricator Lambda with Agent + Tool sub-agents
│   │   └── tools_config.py  #   Tool config loader
│   │
│   ├── workerWrapper/       # Generic execution shell for dynamic agents
│   │   ├── index.py         #   S3 code loading, governance injection, completion events
│   │   └── governance_plugin.py  # GovernedToolHandler (Strands preprocess hook)
│   │
│   ├── activator/           # Agent lifecycle management
│   │   └── index.py         #   Activate/suspend agents via EventBridge events
│   │
│   └── seedConfig/          # CloudFormation CustomResource
│       └── index.py         #   Seeds fabricator config, authority units, global constitution
│
└── app/                     # CDK infrastructure (TypeScript)
    ├── bin/app.ts           #   Entry point, stack wiring
    └── lib/
        ├── fabricStack.ts   #   EventBridge, DynamoDB tables (12 tables), exports
        ├── workerStack.ts   #   Worker Lambda, SQS queues, S3 bucket
        ├── fabricatorStack.ts#  Fabricator Lambda, seed config
        └── arbiterStack.ts  #   Supervisor Lambda, EventBridge rules, SNS
```

## Tech Stack

- **Python 3.13** — All Lambda functions
- **TypeScript** — CDK infrastructure
- **Amazon Bedrock** — Converse API for agent routing (Claude 3.5 Sonnet)
- **Strands Agents SDK** — Worker agent runtime and fabrication
- **Amazon EventBridge** — Inter-agent event routing (Neural Weave)
- **Amazon SQS** — Async agent dispatch with DLQ
- **Amazon DynamoDB** — 12 tables: agent register, workflow state, worker state, tool config, 5 governance tables (authority units, composition contracts, case law, constitutional layers, governance ledger), 2 memory tables (agent metrics, workflow outcomes)
- **Amazon S3** — Versioned agent/tool code storage
- **Amazon SNS** — Governance escalation notifications
- **AWS CDK** — Infrastructure as code (4 stacks)

## Deployment

### Prerequisites

- AWS account with Bedrock model access (Claude 3.5 Sonnet in us-west-2)
- AWS CLI configured with credentials
- Node.js 22+, npm, Python 3.13
- Docker (required for Lambda bundling)

### Deploy

```bash
cd app
npm install
npm run build

# Deploy all stacks (first time)
ENVIRONMENT=dev npx cdk bootstrap --profile <your-profile>
ENVIRONMENT=dev npx cdk deploy --all --require-approval never --profile <your-profile>
```

### Verify

```bash
# Check deployed resources
ENVIRONMENT=dev npx cdk diff --profile <your-profile>

# Submit a test task via EventBridge
aws events put-events \
  --entries '[{
    "Source": "task.request",
    "DetailType": "System-Task",
    "Detail": "{\"task\": \"Create a greeting agent that says hello\"}",
    "EventBusName": "agentic-fabric-dev"
  }]' \
  --profile <your-profile>
```

## Governance

The governance engine is deployed in **bypass mode** by default (`GOVERNANCE_BYPASS=true`). The system functions as a standard Supervisor pattern until governance is activated.

### Activating Governance

1. Verify authority units are seeded: `agentic-fabric-authority-units-dev`
2. Set `GOVERNANCE_BYPASS=false` in `app/lib/arbiterStack.ts`
3. `ENVIRONMENT=dev npx cdk deploy ArbiterStack --require-approval never --profile <your-profile> --exclusively`

Once active, every agent dispatch is evaluated against the authority graph. Actions without a covering authority unit are denied by default (residual authority denial). All permits must survive constitutional review — a global constitution with two invariants is seeded on deploy:
- `no_irreversible_action_without_audit_trail`
- `no_scope_expansion_under_unconfirmed_state`

### Case Law

When the governance engine escalates a conflict it cannot resolve, encode the human resolution:

```bash
cd src/governance
export AWS_PROFILE=<your-profile>
export CASE_LAW_TABLE=agentic-fabric-case-law-dev

# Encode a resolution
python case_law_admin.py encode \
  --pattern '{"target_agent_id": "risky-agent"}' \
  --resolution deny \
  --reason "Blocked after security review" \
  --encoded-by "admin@example.com"

# List all entries
python case_law_admin.py list
```

## Key Design Decisions

**Governance is deterministic.** The governance engine makes no LLM calls. Authority scopes are evaluated as structured tuples. Composition contracts are resolved via four deterministic patterns. All permits must survive mandatory constitutional review — the constitution is the set of things that must always be true, evaluated conjunctively and never bypassed. This satisfies the independence requirement: the governance mechanism is architecturally separate from the agents it governs.

**Residual authority defaults to denial.** Actions not covered by any authority unit are denied, not permitted. This is not brittleness — denied interactions surface genuine gaps in the authority graph. Those gaps are resolved through judgment, then encoded as case law.

**Memory feeds governance; it does not replace it.** Agent metrics and workflow outcomes provide the runtime context that scope conditions evaluate against. The governance engine consumes facts deterministically. Historical patterns inform LLM reasoning through system prompt enrichment. Neither substitutes for explicit authority structures.

**The governance layer is a Lambda Layer, not a service.** Created independently in each consumer stack from the same source. This avoids cross-stack CloudFormation export issues and satisfies the architectural independence requirement — the governance code is separate from the agent code.

## License

This project is part of the Architecting Autonomy research initiative.

## Author

Aaron Sempf — [Architecting Autonomy on Substack](https://aaronsempf.substack.com)
