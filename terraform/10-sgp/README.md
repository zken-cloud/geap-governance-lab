# Layer 1 — Semantic Governance Policies

Not part of the upstream codelab. This is the lab's main extension: a
self-managed SGP engine binding plus five natural-language policies that an LLM
judge evaluates on every governed model call.

```
Agent Gateway ──CONTENT_AUTHZ──▶ agent-gateway-sgp-authz
                                  service = us-central1.sgp.internal
                                       │ private DNS zone sgp.internal.
                                       ▼
                                  sgp-psc-endpoint  10.21.0.2
                                       │ Private Service Connect
                                       ▼
                                  SGP engine service attachment
```

## Prerequisites

- Layer 0 applied (VPC `gateway-vpc`, `agent-gateway`, the MCP servers registered).
- The agent deployed with an **Agent Identity** and attached to the gateway.
  Both properties are immutable; an agent without them cannot be governed.
- `sgp.internal.` present in the gateway's DNS peering domains — set in the
  base tfvars so a base re-apply does not strip it.
- `gcloud`, `curl` and `jq` on PATH: two resource kinds are driven by scripts.

## Apply

```bash
terraform init -backend-config="bucket=<your tfstate bucket>" -backend-config="prefix=sgp"

# project_id, agent_registry_id and mcp_server_ids have no defaults: put them in
# a *.auto.tfvars (gitignored) or pass -var. See the deploy guide for how to
# look the registry IDs up by display name.

# Observe first. Denials are logged, nothing is blocked.
terraform apply -var="enforcement_mode=DRY_RUN"

gcloud logging read "logName=\"projects/$PROJECT_ID/logs/semantic-governance-policy\"" \
  --project=$PROJECT_ID --limit=50 --format=json

# Only after reviewing what *would* have been blocked:
terraform apply -var="enforcement_mode=ENFORCED"
```

Promotion is a metadata edit on one extension, so it applies in seconds and
reverses just as fast.

## What is scripted rather than declared

| Resource | Why | Script |
|---|---|---|
| `semanticGovernancePolicyEngine` | Singleton, `aiplatform v1beta1`, Preview — no provider resource | `scripts/engine.sh` |
| `semanticGovernancePolicies` ×5 | Same | `scripts/policy.sh` |

Both are wrapped in `terraform_data` so they participate in the dependency
graph, re-run when their inputs change, and (for policies) delete on destroy.
The **engine is deliberately never destroyed** — tearing it down would strand
the PSC endpoint and every policy, so `terraform destroy` removes the binding
and leaves the engine ACTIVE.

## Policy authoring notes

- One policy per (MCP server, tool). The API accepts at most one tool per MCP
  server per policy, which is why document search and retrieval are two
  resources rather than one.
- Policies bind to the **Agent Registry agent ID**, not the reasoning-engine ID.
- Constraint text is read by an LLM judge whose rationales are shown verbatim
  to end users. Write it on the assumption that end users will read it.
- `mortgage-minimum-necessary` carries tuned wording. The original literal
  "minimum necessary" phrasing produced enforced false positives — the judge
  denied `get_current_time` and a document search spanning three years instead
  of two. Every hard prohibition survives; the last two sentences are judge
  guidance. Keep that shape if you re-word it.

## Posture

`fail_open = false`. If the engine or the PSC path is unreachable, governed
model calls **fail** rather than bypass policy. This is intentional and differs
from the IAP and Model Armor extensions, which are fail-open. It is also the
first thing to check when every model call starts failing at once.
