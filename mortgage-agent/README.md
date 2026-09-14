# Mortgage Assistant Agent

ADK **2.x** (`google-adk 2.9.0`, Agent Engine SDK 2.1) mortgage assistant agent deployed to
Agent Runtime with Agent Identity, attached to `agent-gateway`. It discovers its three MCP
servers (legacy DMS, income verification, corporate email — Cloud Run services) from the
Agent Registry at startup.

## Provenance — vendored 2026-08-17

Vendored from `demos/agent-gateway/src/mortgage-agent/` in
[GoogleCloudPlatform/cloud-networking-solutions](https://github.com/GoogleCloudPlatform/cloud-networking-solutions),
where it lived only as an uncommitted working-tree change. **This copy is now the
source of truth** — edit here, not in the clone.

It diverges from upstream in two ways:

- **`_handle_sgp_denial`**, the `after_model_callback` in `agent/agent.py` that turns an
  SGP policy denial into a bounded `policy_replan` feedback loop instead of letting it
  end the turn. The stock codelab agent has no denial handling. That callback is
  Chapter 4a of the demo guide, where it is presented as a best practice.
- **Authentication to Cloud Run is the agent's own identity** (since 2026-09-12).
  `_agent_header_provider` hands ADK a per-server `header_provider` that attaches an OIDC
  ID token from the runtime's metadata server — subject = this agent's SPIFFE ID — and Cloud
  Run honours it because the agent principal holds `roles/run.invoker` on each MCP service.
  The codelab impersonates a shared `agent-mcp-invoker@` service account; that was verified
  unnecessary and removed here.
  `header_provider` is the hook that works on ADK 2.x: a custom httpx client is bypassed by
  the runtime's mTLS transport, and a `SERVICE_ACCOUNT` `auth_credential` is not exchanged
  for a bearer scheme in 2.9.
- **Upgraded to ADK 2.x** on 2026-09-12 (upstream is on 1.34). `[mcp]` is a required extra
  since 2.0; the operator venv is pinned to Python 3.12 (`.python-version`) to match the
  runtime container so the pickle round-trips cleanly.

## Layout

```
agent/agent.py     the agent: ID-token header provider, registry discovery, instruction,
                   the two governance callbacks (_handle_tool_error, _handle_sgp_denial)
agent/tools.py     utility tools (get_current_time, list_mcp_connections)
agent/__init__.py  Agent Runtime import-time environment
deploy.py          what is specific to this agent; mechanics in ../shared/agent_deploy.py
../shared/         agent_deploy.py (deploy/update on Agent Runtime), gemini_enterprise.py (GE registration)
tests/             35+ unit tests — uv sync --extra dev && uv run pytest
```

## Deploy

Redeploy in place (identity, gateway, IAM and GE registration are preserved):

```bash
PROJECT_ID=$PROJECT_ID GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token) \
uv run python deploy.py \
  --update=projects/<number>/locations/us-central1/reasoningEngines/<id> \
  --agent-gateway=projects/$PROJECT_ID/locations/us-central1/agentGateways/agent-gateway
```

Region and model have defaults (`us-central1`, `gemini-3.5-flash`); every agent here is deployed
with Agent Identity, so there is no flag for it.

Omit `--update` to create a new engine: the script creates an identity shell first so
the principal exists, then pushes the code. A new engine needs two grants per MCP
server before its tools work — `roles/run.invoker` on the Cloud Run service and
`roles/iap.egressor` on the registry `mcpServer` resource — both naming
`principal://agents.global.org-…/reasoningEngines/<ID>`. Gemini Enterprise registration is a separate step:
`python ../shared/gemini_enterprise.py --app-id ... --engine ... --display-name ... --agent-name ...`
(needs `--oauth-client-id` and `$OAUTH_CLIENT_SECRET`).

The deploy requirements list and `pyproject.toml` pin the same `google-adk` and
`google-cloud-aiplatform` ranges on purpose: the operator pickles the app with one SDK
version and the container unpickles it with the other.

## How a tool call is authorised

```
Agent Runtime ── egress ──► Agent Gateway ──► Cloud Run MCP server
   (Agent Identity)           IAP: iap.egressor        run.invoker on the
                              on the registry          service, checked
                              mcpServer, per tool      against the ID token
                              + Model Armor + SGP      the agent minted
```

Same principal, two independent checks. Tests need the dev extra first —
`uv sync --extra dev && uv run pytest`. Without it, `uv run pytest` falls back to a
*system* pytest and fails collection with a misleading `No module named 'google.adk'`.

## Local testing

```bash
uv sync
adk web
```
