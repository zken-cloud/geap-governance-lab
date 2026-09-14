# LangGraph Agent on Cloud Run — the DIY control case

A minimal mortgage-intake agent built with **LangGraph** and hosted on **Cloud Run**,
deliberately *not* on Agent Runtime. It exists to answer one governance question:

> What can GEAP enforce against an agent it does not host?

## What it demonstrates

Registered in the Agent Registry as **"Langraph Agent on Cloud Run"**, it appears
alongside the Agent Runtime agents but differs in one decisive way:

| Agent | RuntimeIdentity |
|---|---|
| Mortgage Assistant Agent | `principal://agents.global.org-…/reasoningEngines/<engine id>` |
| Partner Access Agent | `principal://agents.global.org-…/reasoningEngines/<engine id>` |
| **Langraph Agent on Cloud Run** | **none** |

Registration is a catalog fact, not an identity grant. `Agent.attributes` is
output-only and `AgentSpec` carries no identity field, so a DIY agent gets a
`RuntimeReference` and nothing else.

The consequence is asymmetric:

- **As a policy target — works.** `roles/iap.egressor` binds on its registry agent
  resource (`iap_web/agentRegistry/agents/{ID}`), naming the *caller's* principal.
  An Agent Runtime or Gemini Enterprise caller reaching it is governed normally.
- **As a policy caller — does not work.** It has no principal to name, and its
  egress never traverses the Agent Gateway (the four authz policies all target
  `agentGateways/agent-gateway`, and only Agent Runtime and Gemini Enterprise are
  supported traffic sources). Its outbound calls are ungoverned.

## Deploy

```bash
export PROJECT_ID=... REGION=us-central1
./build.sh v1
gcloud run deploy langgraph-agent \
  --project=$PROJECT_ID --region=$REGION \
  --image=$REGION-docker.pkg.dev/$PROJECT_ID/gateway-docker/langgraph-agent:v1 \
  --service-account=langgraph-agent@$PROJECT_ID.iam.gserviceaccount.com \
  --set-env-vars=PROJECT_ID=$PROJECT_ID,LOCATION=global,MODEL=gemini-3.7-flash \
  --no-allow-unauthenticated
```

Note `LOCATION=global`: the `gemini-3.x` publisher models 404 on the regional Vertex
`publishers/google/models/...` path; the `global` endpoint serves them.

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/` | — | health + identity summary (`agent_identity` is `null` by design) |
| `POST` | `/invoke` | `{"message": str}` | `{"reply": str}` |

Requires an identity token — the service is deployed `--no-allow-unauthenticated`:

```bash
curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -X POST -H 'Content-Type: application/json' \
  -d '{"message":"What documents do I need to start a mortgage application?"}' \
  "$(gcloud run services describe langgraph-agent --region=$REGION --format='value(status.url)')/invoke"
```

## Not in Terraform

Deployed by hand, like the Auth Manager layer. The Cloud Run service, its service
account (`langgraph-agent@`, `roles/aiplatform.user`), the registry service
`langgraph-agent`, and the target-side IAP binding are all unmanaged.
