"""Referral Desk Agent — an Agent Runtime caller for the DIY LangGraph agent.

Its only job is to call "Langraph Agent on Cloud Run" (a DIY agent with no Agent
Identity) so that the agent-to-agent leg can be governed and demonstrated. The
call leaves Agent Runtime, is intercepted by the Agent Gateway, and is authorized
against the IAP policy on the *target's* Agent Registry resource.

Remove `roles/iap.egressor` from that target policy and this agent's tool call
comes back 403 from the gateway — the target never runs.

Authentication to Cloud Run is the agent's own identity: `fetch_id_token` asks
the runtime's metadata server for an OIDC token whose subject is this agent's
SPIFFE ID, and Cloud Run honours it once the agent principal holds
`roles/run.invoker` on the service. No service account is involved.
"""

import os
from urllib.parse import urlparse

import google.auth.transport.requests
import google.oauth2.id_token
import httpx
from google.adk.agents import LlmAgent

MODEL_NAME = os.environ.get("MODEL_NAME", "gemini-3.5-flash")


def ask_intake_agent(question: str) -> dict:
    """Refer a mortgage-intake question to the partner intake agent.

    Use this for any question about which documents, forms, or figures a
    mortgage application requires. Returns the partner agent's answer.

    Args:
        question: The mortgage-intake question to refer, in plain English.

    Returns:
        A dict with `status`, and either `answer` or `error`.
    """
    # Read at call time: deploy.py imports this module locally to pickle it.
    target_url = os.environ["TARGET_AGENT_URL"]  # set by deploy.py from --target-agent-url
    audience = "{0.scheme}://{0.netloc}".format(urlparse(target_url))
    try:
        token = google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(), audience)
        response = httpx.post(
            f"{target_url}/invoke",
            json={"message": question},
            headers={"Authorization": f"Bearer {token}"},
            timeout=120.0,
        )
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    if response.status_code != 200:
        # The governed denial lands here: the Agent Gateway returns 403 before
        # the request ever reaches the target agent.
        return {
            "status": "error",
            "error": f"HTTP {response.status_code} calling the partner intake agent. Body: {response.text[:300]}",
        }
    return {"status": "ok", "answer": response.json().get("reply", "")}


root_agent = LlmAgent(
    name="referral_desk_agent",
    model=MODEL_NAME,
    description="Refers mortgage-intake questions to a partner intake agent.",
    instruction=(
        "You are a referral desk. When the user asks anything about the documents, "
        "forms, or figures a mortgage application requires, call the "
        "`ask_intake_agent` tool and relay its answer.\n\n"
        "If the tool returns an error, say plainly that the referral was refused, "
        "quote the status code, and explain that an access policy blocked the call "
        "to the partner agent. Never invent an answer of your own in that case."
    ),
    tools=[ask_intake_agent],
)
