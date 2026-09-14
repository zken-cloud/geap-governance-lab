"""Register a deployed Agent Runtime engine in a Gemini Enterprise app.

    python -m gemini_enterprise --project P --app-id APP \\
        --engine projects/N/locations/R/reasoningEngines/ID \\
        --display-name "Mortgage Agent" --agent-name mortgage-agent \\
        --oauth-client-id ID  # $OAUTH_CLIENT_SECRET in the environment

Idempotent per display name: an existing agent with the same name is deleted
first (authorizations cannot be deleted while linked), stale authorizations
with the agent-name prefix are removed, then a fresh authorization and agent
are created. The authorization id is timestamp-suffixed because the Gemini
Enterprise backend needs that format for OAuth token storage; a plain id
produces an endless consent loop.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import google.auth
from google.auth.transport.requests import AuthorizedSession

API = "https://global-discoveryengine.googleapis.com/v1alpha"
REDIRECT = "https://vertexaisearch.cloud.google.com/static/oauth/oauth.html"


def register(
    *,
    project: str,
    app_id: str,
    engine: str,
    display_name: str,
    agent_name: str,
    description: str,
    oauth_client_id: str,
    oauth_client_secret: str,
) -> str:
    """Register `engine` in the app and return the new agent resource name."""
    creds, _ = google.auth.default()
    http = AuthorizedSession(creds)
    http.headers["X-Goog-User-Project"] = project
    base = f"{API}/projects/{project}/locations/global"
    agents = f"{base}/collections/default_collection/engines/{app_id}/assistants/default_assistant/agents"

    for agent in http.get(agents).json().get("agents", []):
        if agent.get("displayName") == display_name:
            print(f"Deleting existing agent {agent['name']}")
            http.delete(f"{API}/{agent['name']}").raise_for_status()

    prefix = f"projects/{project}/locations/global/authorizations/{agent_name}"
    for auth in http.get(f"{base}/authorizations").json().get("authorizations", []):
        if auth.get("name") == prefix or auth.get("name", "").startswith(f"{prefix}_"):
            print(f"Deleting stale authorization {auth['name']}")
            http.delete(f"{API}/{auth['name']}").raise_for_status()

    auth_id = f"{agent_name}_{int(time.time() * 1000)}"
    authorization_uri = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={oauth_client_id}&redirect_uri={REDIRECT}"
        "&scope=https://www.googleapis.com/auth/cloud-platform"
        "&include_granted_scopes=true&response_type=code&access_type=offline&prompt=consent"
    )
    resp = http.post(
        f"{base}/authorizations",
        params={"authorizationId": auth_id},
        json={
            "displayName": auth_id,
            "serverSideOauth2": {
                "clientId": oauth_client_id,
                "clientSecret": oauth_client_secret,
                "tokenUri": "https://oauth2.googleapis.com/token",
                "authorizationUri": authorization_uri,
            },
        },
    )
    resp.raise_for_status()
    authorization = resp.json()["name"]
    print(f"Authorization created: {authorization}")

    resp = http.post(
        agents,
        json={
            "displayName": display_name,
            "description": description,
            "adk_agent_definition": {"provisioned_reasoning_engine": {"reasoning_engine": engine}},
            "authorization_config": {"tool_authorizations": [authorization]},
            "sharingConfig": {"scope": "ALL_USERS"},
            "agentInvocationSpec": {"invocationMode": "AUTOMATIC"},
        },
    )
    resp.raise_for_status()
    name = resp.json()["name"]
    print(f"Agent registered: {name}")
    return name


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--project", default=os.environ.get("PROJECT_ID"), required="PROJECT_ID" not in os.environ)
    p.add_argument("--app-id", required=True)
    p.add_argument("--engine", required=True, help="full reasoningEngines resource name")
    p.add_argument("--display-name", required=True)
    p.add_argument(
        "--agent-name",
        required=True,
        help="authorization/agent id prefix, e.g. mortgage-agent",
    )
    p.add_argument("--description", default="")
    p.add_argument("--oauth-client-id", default=os.environ.get("OAUTH_CLIENT_ID"))
    args = p.parse_args(argv)
    secret = os.environ.get("OAUTH_CLIENT_SECRET")
    if not args.oauth_client_id or not secret:
        sys.exit("--oauth-client-id and $OAUTH_CLIENT_SECRET are required")
    register(
        project=args.project,
        app_id=args.app_id,
        engine=args.engine,
        display_name=args.display_name,
        agent_name=args.agent_name,
        description=args.description,
        oauth_client_id=args.oauth_client_id,
        oauth_client_secret=secret,
    )


if __name__ == "__main__":
    main()
