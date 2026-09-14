"""Deploy the Mortgage Assistant Agent to Agent Runtime.

    PROJECT_ID=... GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token) uv run python deploy.py \\
        --update=projects/<number>/locations/us-central1/reasoningEngines/<id> \\
        --agent-gateway=projects/$PROJECT_ID/locations/us-central1/agentGateways/agent-gateway

The mechanics live in ../shared/agent_deploy.py; this file only says what is
specific to this agent. Gemini Enterprise registration is a separate step:
../shared/gemini_enterprise.py.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shared"))

import agent_deploy  # noqa: E402  (needs the sys.path line above)


def _env(args) -> dict[str, str]:
    env = {
        # Registry discovery scope (agent/agent.py reads these at import time).
        "MCP_REGISTRY_PROJECT": args.project,
        "MCP_REGISTRY_LOCATION": args.region,
        # Denied MCP calls (gateway 403) fail fast instead of hanging the turn.
        "ADK_ENABLE_MCP_GRACEFUL_ERROR_HANDLING": "true",
    }
    if args.registry_filter:
        env["MCP_REGISTRY_FILTER"] = args.registry_filter
    return env


SPEC = agent_deploy.AgentSpec(
    display_name="Mortgage Assistant Agent",
    description=(
        "ADK mortgage assistant agent connecting to legacy DMS, income verification, and corporate email services."
    ),
    agent_dir=HERE,
    env=_env,
    add_args=lambda p: p.add_argument(
        "--registry-filter", default=None, help="Google API list filter for the registry's mcpServers"
    ),
    requirements=["opentelemetry-instrumentation-google-genai", "opentelemetry-exporter-gcp-logging"],
    min_instances=2,
)

if __name__ == "__main__":
    agent_deploy.main(SPEC)
