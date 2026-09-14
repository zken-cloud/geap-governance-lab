"""Deploy the Referral Desk Agent to Agent Runtime.

    GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token) \\
    ../mortgage-agent/.venv/bin/python deploy.py \\
        --agent-gateway=projects/$PROJECT_ID/locations/us-central1/agentGateways/agent-gateway \\
        --target-agent-url=https://langgraph-agent-....run.app

The mechanics live in ../shared/agent_deploy.py; this file only says what is
specific to this agent. Reuses the mortgage agent's venv.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shared"))

import agent_deploy  # noqa: E402  (needs the sys.path line above)

SPEC = agent_deploy.AgentSpec(
    display_name="Referral Desk Agent",
    description="Refers mortgage-intake questions to the DIY LangGraph agent on Cloud Run.",
    agent_dir=HERE,
    env=lambda args: {"TARGET_AGENT_URL": args.target_agent_url},
    add_args=lambda p: p.add_argument(
        "--target-agent-url",
        required=True,
        help="the DIY agent's Cloud Run URL",
    ),
)

if __name__ == "__main__":
    agent_deploy.main(SPEC)
