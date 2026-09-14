# geap-governance-lab

Everything the **Governed Agent Platform demo** adds on top of the
[Agent Gateway codelab](https://codelabs.developers.google.com/cloudnet-agent-gateway):
Semantic Governance Policies, a self-correcting mortgage agent, a synthetic
partner SaaS, Agent Identity Auth Manager with a second governed agent, and a
DIY LangGraph agent governed as a policy target.

**Start with the codelab, then follow the step-by-step guide:**
<https://geap-governance.cedemo.app/deploy.html> (the *Deploy the deltas* tab of
the demo guide site). Every command there discovers your own project's IDs and
explains what it changes. Nothing in this repo assumes a particular project.

```
terraform/10-sgp/             Semantic Governance Policy layer — engine binding, PSC, DNS,
                              fail-closed authz extension, five natural-language policies
terraform/30-partner-services/ Cymbal Partner Services on Cloud Run + its generated secrets
partner-services/             the synthetic SaaS itself (API key / 2LO / 3LO + OAuth2 server)
mortgage-agent/               the codelab agent, upgraded: ADK 2.x, own-identity Cloud Run
                              auth, SGP denial -> policy_replan loop (35 unit tests)
auth-manager-agent/           Partner Access Agent — credentials vaulted in Auth Manager
caller-agent/                 Referral Desk Agent — governed caller of the DIY agent
langgraph-agent/              the DIY agent on plain Cloud Run (no Agent Identity)
shared/                       agent_deploy.py (Agent Runtime deploy) and gemini_enterprise.py
```

## Prerequisites

The codelab finished end to end and still deployed; `gcloud`, `terraform` >= 1.9,
`jq`, `curl`, [`uv`](https://docs.astral.sh/uv/) and Python 3.12.

## Conventions

- **No project-specific defaults.** `project_id`, registry IDs, service URLs and
  the like are required inputs — Terraform variables without defaults, required
  CLI flags, or environment variables the deploy scripts set. Per-deployment
  inputs (`backend.conf`, `*.auto.tfvars`, `terraform.tfvars`) are gitignored.
- **Preview APIs are driven over REST.** `gcloud` and the Terraform provider lag
  the SGP, Agent Registry and Auth Manager surfaces; where there is no resource
  type, a small script wrapped in `terraform_data` does the work idempotently.
- **Every agent is deployed with an Agent Identity** and attached to
  `agent-gateway`; both are immutable on an engine, so there is no flag for them.

## Tests

```bash
(cd mortgage-agent     && uv sync --extra dev && uv run pytest)
(cd auth-manager-agent && uv sync --extra dev && uv run pytest)
(cd partner-services   && uv venv .venv && uv pip install -p .venv/bin/python -r requirements.txt httpx && .venv/bin/python test_modes.py)
```
