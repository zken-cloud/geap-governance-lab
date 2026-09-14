# Referral Desk Agent — the governed caller

A deliberately tiny ADK agent on **Agent Runtime**, deployed with Agent Identity and
attached to `agent-gateway`. Its only tool refers a mortgage-intake question to
**"Langraph Agent on Cloud Run"** (see [`../langgraph-agent/`](../langgraph-agent/)),
so the agent-to-agent leg can be governed and demonstrated.

It exists to prove one thing: a DIY agent the platform does not host is still
governable **as a policy target**. Chapter 3a of the demo guide runs this.

| | |
|---|---|
| Engine | created by `deploy.py` (Agent Runtime, Agent Identity) |
| Identity | `principal://agents.global.org-<ORG>.system.id.goog/…/reasoningEngines/<ENGINE_ID>` |
| Target | the `langgraph-agent` Cloud Run URL |
| Gateway policy | `roles/iap.egressor` on the DIY agent's registry agent resource |
| Cloud Run policy | `roles/run.invoker` on `langgraph-agent`, granted to the principal above |

## The demo

The allow policy lives on the **target's** registry agent resource and names the
**caller's** principal. With the Referral Desk Agent absent from it:

```
HTTP 403 calling the partner intake agent. Body: Egress request is not authorized.
```

The gateway stops the call before it leaves Agent Runtime — the DIY agent never runs.
Add the caller's principal to that binding, wait ~60–90 s for propagation, re-run, and
the referral succeeds. No redeploy of either agent.

**The demo ships in the denied state.** Grant during the demo, then revoke to restore.

## Auth

The tool authenticates to Cloud Run as **the agent itself**:
`google.oauth2.id_token.fetch_id_token(request, audience)` asks the runtime's metadata
server for an OIDC token whose subject is this agent's SPIFFE ID, and Cloud Run honours
it because the agent principal holds `roles/run.invoker` on `langgraph-agent`. No
service account, no impersonation.

Until 2026-09-12 the tool impersonated a shared `agent-mcp-invoker@` service account,
following the codelab, which predates Cloud Run accepting agent principals. That was
verified unnecessary and removed.
The agent's ADC *access* token is still rejected by Cloud Run (401) — it must be an ID
token.

## Deploy

```bash
PROJECT_ID=$PROJECT_ID GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token) \
../mortgage-agent/.venv/bin/python deploy.py \
  --agent-gateway="projects/$PROJECT_ID/locations/us-central1/agentGateways/agent-gateway" \
  --target-agent-url="$LANGGRAPH_URL"
```

Add `--update=<full reasoningEngines name>` to redeploy an existing engine in place.
Without it the script creates a new engine (identity shell first, then code). Then grant
the new principal `roles/run.invoker` on the target service. `deploy.py` reuses the
mortgage agent's venv (Python 3.12, `google-adk 2.9.0`, Agent Engine SDK 2.1 — ADK 2.x
since 2026-09-12) and pins the same versions in its requirements list. Not in Terraform.
