"""A DIY mortgage-intake agent: LangGraph on Cloud Run.

Deliberately *not* on Agent Runtime. This is the control case for the governance
question "what can GEAP enforce against an agent it does not host?" -- it is
registered in the Agent Registry but, having no Agent Identity, it carries no
`RuntimeIdentity` principal and its egress does not traverse the Agent Gateway.

Endpoints:
    GET  /          health + identity summary
    POST /invoke    {"message": str, "thread_id": str?} -> {"reply": str}
"""

import os

from fastapi import FastAPI
from google import genai
from google.genai import types
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel
from typing_extensions import TypedDict

PROJECT_ID = os.environ["PROJECT_ID"]  # set by `gcloud run deploy --set-env-vars`
# Gemini 3.x is served from the `global` endpoint only -- the regional
# us-central1 publisher path 404s for every 3.x model.
LOCATION = os.environ.get("LOCATION", "global")
MODEL = os.environ.get("MODEL", "gemini-3.7-flash")

SYSTEM_INSTRUCTION = (
    "You are a mortgage intake assistant. Help the user assemble the documents "
    "and figures a mortgage application needs. You have no tools and no access "
    "to applicant records, so never claim to have looked anything up, and never "
    "make a final lending, approval, or underwriting decision -- those belong to "
    "a human underwriter."
)

_client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)


class State(TypedDict):
    message: str
    reply: str


def respond(state: State) -> State:
    """The single reasoning node: one grounded Gemini turn."""
    result = _client.models.generate_content(
        model=MODEL,
        contents=state["message"],
        config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION),
    )
    return {"message": state["message"], "reply": result.text or ""}


_builder = StateGraph(State)
_builder.add_node("respond", respond)
_builder.add_edge(START, "respond")
_builder.add_edge("respond", END)
GRAPH = _builder.compile()

app = FastAPI(title="LangGraph Agent on Cloud Run")


class InvokeRequest(BaseModel):
    message: str


@app.get("/")
def health() -> dict:
    return {
        "agent": "LangGraph Agent on Cloud Run",
        "framework": "langgraph",
        "runtime": "cloud-run",
        "model": MODEL,
        # Stated plainly because it is the whole point of this deployment.
        "agent_identity": None,
    }


@app.post("/invoke")
def invoke(request: InvokeRequest) -> dict:
    final = GRAPH.invoke({"message": request.message, "reply": ""})
    return {"reply": final["reply"]}
