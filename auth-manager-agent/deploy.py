"""Deploy the Partner Access Agent to Agent Runtime.

    PROJECT_ID=... GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token) uv run python deploy.py \\
        --agent-gateway=projects/$PROJECT_ID/locations/us-central1/agentGateways/agent-gateway \\
        --partner-base-url=https://cymbal-partner-services-....run.app \\
        --continue-uri=https://<where the 3LO consent redirect should land>/
    # add --update=<full reasoningEngines name> to redeploy an existing engine in place

The mechanics live in ../shared/agent_deploy.py; this file only says what is
specific to this agent.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shared"))

import agent_deploy  # noqa: E402  (needs the sys.path line above)


def _env(args) -> dict[str, str]:
    return {
        "PARTNER_BASE_URL": args.partner_base_url,
        "AUTH_MANAGER_CONTINUE_URI": args.continue_uri,
    }


def _args(p) -> None:
    p.add_argument("--partner-base-url", required=True, help="the Cymbal partner SaaS base URL (terraform output service_url)")
    p.add_argument("--continue-uri", required=True, help="where the 3LO consent redirect lands after the user approves")


SPEC = agent_deploy.AgentSpec(
    display_name="Partner Access Agent",
    description=("ADK agent reaching Cymbal partner systems using credentials vaulted in Agent Identity Auth Manager."),
    agent_dir=HERE,
    env=_env,
    add_args=_args,
    requirements=["opentelemetry-instrumentation-google-genai", "opentelemetry-exporter-gcp-logging"],
    min_instances=2,
)

if __name__ == "__main__":
    agent_deploy.main(SPEC)
