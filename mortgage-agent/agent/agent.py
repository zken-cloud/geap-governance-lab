# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""ADK agent definition for the mortgage assistant with MCP tool connections."""

import base64
import json
import logging
import os
import re
import time
from typing import Any, Callable
from urllib.parse import urlparse

import google.auth.transport.requests
import google.oauth2.id_token
import httpx
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.llm_agent import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types as genai_types

from . import tools

logger = logging.getLogger(__name__)


# ── Identity: how this agent authenticates to a Cloud Run MCP server ─────────


class _AgentIDToken:
    """This agent's own OIDC ID token for one Cloud Run origin, cached until expiry.

    `fetch_id_token` asks the runtime's metadata server. Inside Agent Runtime with
    AGENT_IDENTITY the token's subject is the agent's SPIFFE ID, and Cloud Run
    honours it once the agent principal holds `roles/run.invoker` on the service.
    No service account, no impersonation.
    """

    def __init__(self, audience: str):
        self._audience = audience
        self._token: str | None = None
        self._expires_at = 0.0

    def __call__(self) -> str:
        if self._token is None or time.time() > self._expires_at - 60:
            self._token = google.oauth2.id_token.fetch_id_token(
                google.auth.transport.requests.Request(), self._audience
            )
            self._expires_at = _jwt_expiry(self._token)
        return self._token


def _jwt_expiry(token: str) -> float:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return float(json.loads(base64.urlsafe_b64decode(payload)).get("exp", time.time() + 300))


def _agent_header_provider(mcp_url: str) -> Callable[[ReadonlyContext], dict[str, str]]:
    """ADK `header_provider`: called on every MCP session and tool call.

    This is the hook ADK 2.x reads for both `tools/list` and `tools/call`. (An
    `auth_credential` of type SERVICE_ACCOUNT is not exchanged for a bearer
    scheme in 2.9, and a custom httpx client is bypassed once the runtime's
    mTLS transport is in play — headers are the reliable path.)
    """
    token = _AgentIDToken("{0.scheme}://{0.netloc}".format(urlparse(mcp_url)))
    return lambda _context: {"Authorization": f"Bearer {token()}"}


# ── Tool discovery from the Agent Registry ───────────────────────────────────

# What the registry returned, plus each toolset's resolved prefix and tool
# names. Read by the instruction template (so the model sees exact tool names)
# and by the `list_mcp_connections` tool.
DISCOVERED_MCP_SERVERS: list[dict[str, Any]] = []

# Discovery runs once per process. Agent Runtime rebuilds the agent on every
# unpickle (_PickleSafeAgent), and a second MCPSessionManager in one process
# trips "Context has already been used to create a Connection" in anyio. Empty
# results are cached too, so membership is frozen until redeploy.
_DISCOVERY_CACHE: tuple[list, list[dict[str, Any]]] | None = None

_SERVICE_DESCRIPTIONS: dict[str, str] = {
    "legacy-dms": (
        "the legacy document management system. Use to fetch tax returns, pay stubs, "
        "bank statements, and other applicant documents."
    ),
    "corporate-email": (
        "the corporate communications system. Use to read the corporate inbox. "
        "Write operations like sending emails may be restricted by the "
        "authorization gateway."
    ),
    "income-verification": (
        "a third-party income verification vendor. Use to verify reported income "
        "against employer records and tax filings."
    ),
}


def _discover_mcp_toolsets() -> list:
    """Return ADK toolsets for every MCP server in the Agent Registry.

    Project and region come from MCP_REGISTRY_PROJECT / MCP_REGISTRY_LOCATION
    (set by deploy.py); MCP_REGISTRY_FILTER optionally narrows the list.
    Failures are logged and yield [] so the agent still boots with utility tools.
    """
    global _DISCOVERY_CACHE
    if _DISCOVERY_CACHE is not None:
        toolsets, discovered = _DISCOVERY_CACHE
        DISCOVERED_MCP_SERVERS[:] = discovered
        return toolsets

    DISCOVERED_MCP_SERVERS.clear()
    toolsets: list = []
    try:
        toolsets = _registry_toolsets()
    except Exception:
        logger.exception("MCP registry discovery failed; continuing with utility tools only")
    _DISCOVERY_CACHE = (toolsets, list(DISCOVERED_MCP_SERVERS))
    return toolsets


def _tool_prefix(display_name: str) -> str:
    """Same cleaning ADK's registry client applies: `legacy-dms` -> `legacy_dms`."""
    clean = re.sub(r"_+", "_", re.sub(r"[^a-zA-Z0-9_]", "_", display_name)).strip("_")
    return clean if clean and (clean[0].isalpha() or clean[0] == "_") else f"_{clean}"


def _registry_toolsets() -> list:
    project = os.environ.get("MCP_REGISTRY_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("MCP_REGISTRY_LOCATION")
    if not project or not location:
        logger.warning("MCP registry discovery skipped: set MCP_REGISTRY_PROJECT and MCP_REGISTRY_LOCATION")
        return []

    # Needs google-adk[a2a,agent-identity,mcp]; without the extras these imports fail.
    from google.adk.integrations.agent_registry.agent_registry import (
        AgentRegistry,
        AgentRegistrySingleMcpToolset,
    )
    from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

    registry = AgentRegistry(project_id=project, location=location)
    servers = registry.list_mcp_servers(filter_str=os.environ.get("MCP_REGISTRY_FILTER")).get("mcpServers", [])

    toolsets = []
    for server in servers:
        name = server.get("name")
        url = next((i.get("url") for i in server.get("interfaces", []) if i.get("url")), None)
        if not name or not url:
            continue
        # Built directly rather than via registry.get_mcp_toolset() so the
        # header provider can be per server (each Cloud Run origin is its own
        # ID-token audience). ADK keeps its 5 s connection timeout, so a denied
        # call still fails fast.
        prefix = _tool_prefix(server.get("displayName") or name)
        toolset = AgentRegistrySingleMcpToolset(
            destination_resource_id=server.get("mcpServerId"),
            connection_params=StreamableHTTPConnectionParams(url=url),
            tool_name_prefix=prefix,
            header_provider=_agent_header_provider(url),
        )
        toolsets.append(toolset)
        DISCOVERED_MCP_SERVERS.append(
            {
                "name": server.get("displayName") or name,
                "resource_name": name,
                "tool_name_prefix": prefix,
                "resolved_url": url,
                "tools": [t["name"] for t in server.get("tools", []) if t.get("name")],
            }
        )
        logger.info("MCP toolset %s: prefix=%s url=%s", name, prefix, url)

    if not toolsets:
        logger.warning("MCP registry %s/%s returned no usable servers", project, location)
    return toolsets


# ── Instruction ──────────────────────────────────────────────────────────────

_INSTRUCTION_TEMPLATE = """You are a mortgage underwriting assistant. You help loan officers process
mortgage applications by retrieving documents, verifying income, and communicating results.

You connect to backend systems through an Agent Gateway. The set of available tools is
discovered from the Agent Registry at startup and may change between deployments.
**Only call tools by the exact names listed below.** Tool names use underscores as
separators (e.g. `legacy_dms_search_documents`); never use a colon (`:`), slash, dot,
or any other separator.

**MCP services discovered from the registry:**
{mcp_services_doc}

**Workflow:**
1. Fetch the applicant's tax documents using the document management tools.
2. Verify the applicant's reported income using the income verification tools.
3. Compare the figures from both sources and note any discrepancies.
4. Summarize your findings clearly for the loan officer.

**Rules:**
- NEVER fabricate or estimate financial figures. Only report data returned by tools.
- Always cite which tool/system provided each piece of data.
- If a tool call fails or returns an error, report the error honestly to the user.
- **Never invent tool names.** Only call tools whose exact names appear in the MCP
services list above or in the utility tools list below. If no listed tool matches your
need, tell the user you cannot perform that operation rather than guessing at a name.
- Be concise and professional in all responses.
- When presenting tax return or applicant data, ALWAYS include the SSN field and display its value
exactly as returned by the tool (e.g. "[US_SOCIAL_SECURITY_NUMBER]"). Never omit SSN fields.

You also have utility tools:
- get_current_time: Returns the current time in any timezone.
- list_mcp_connections: Shows which MCP servers were discovered from the registry.
- policy_replan: Governance feedback channel. When a proposed action is denied by a
semantic governance policy, this tool returns the denial reason — adjust your approach
(narrower scope, different tool) and continue helping the user; do not abandon the
request. Never call this tool yourself."""


def _render_mcp_services_doc() -> str:
    """Enumerate the exact prefixed tool names the model may call.

    The `prefix_*` wildcard is only a fallback for servers that advertised no
    tool list — a wildcard next to known names invites invented names.
    """
    if not DISCOVERED_MCP_SERVERS:
        return "_(no MCP services discovered — only utility tools are available)_"
    lines: list[str] = []
    for entry in DISCOVERED_MCP_SERVERS:
        prefix = entry.get("tool_name_prefix")
        name = entry.get("name") or entry.get("resource_name") or "?"
        tool_names = entry.get("tools") or []
        desc = _SERVICE_DESCRIPTIONS.get(name)
        suffix = f" — connects to {desc}" if desc else ""
        if prefix and tool_names:
            full = ", ".join(f"`{prefix}_{t}`" for t in tool_names)
            lines.append(f"- **{name}** (tools: {full}){suffix}")
        elif prefix:
            lines.append(f"- **{name}** (tools prefixed `{prefix}_*`){suffix}")
        else:
            lines.append(f"- **{name}** (no tools advertised){suffix}")
    return "\n".join(lines)


# ── Governance callbacks ─────────────────────────────────────────────────────


def _find_http_status_error(exc: BaseException, status_code: int) -> bool:
    """Search exception chains and ExceptionGroups for an HTTPStatusError."""
    seen: set[int] = set()
    queue: list[BaseException] = [exc]
    while queue:
        current = queue.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, httpx.HTTPStatusError) and current.response.status_code == status_code:
            return True
        if current.__cause__ is not None:
            queue.append(current.__cause__)
        if current.__context__ is not None:
            queue.append(current.__context__)
        if isinstance(current, BaseExceptionGroup):
            queue.extend(current.exceptions)
    return False


def _handle_tool_error(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext, error: Exception
) -> dict | None:
    """on_tool_error_callback: turn an IAP 403 into a tool result the model can act on.

    Left alone, the model re-calls a denied tool on every turn, paying the authz
    extension's per-call budget each time. Returning a dict instead of re-raising
    tells it the denial is final for this session.
    """
    if not _find_http_status_error(error, 403):
        return None
    logger.warning("Tool %s denied by authorization policy (403)", tool.name)
    return {
        "error": (
            f"The '{tool.name}' tool call was blocked by authorization policies. "
            "Do not call this tool again in this session — tell the user this action "
            "is blocked by authorization policies, and proceed with other tools."
        ),
    }


# The gateway's semantic-governance extension (SGP, CONTENT_AUTHZ) can replace
# a model response whose proposed tool call violates policy with a synthesized
# text refusal of the form:
#   "I am sorry, but I cannot proceed with that request. Reason: <rationale>"
# Left alone, that text ends the turn: the agent abandons the whole task on the
# first denial even when a narrower, compliant call would succeed.
# _handle_sgp_denial (after_model_callback) rewrites such refusals into a
# `policy_replan` tool call so the model receives the denial rationale as tool
# feedback and can retry compliantly. Bounded per invocation so a hard denial
# cannot ping-pong against the policy judge (each retry costs an SGP
# evaluation, ~2.5k judge tokens).
_SGP_DENIAL_PREFIX = "I am sorry, but I cannot proceed with that request."
_REPLAN_STATE_KEY = "_sgp_replan"
_MAX_REPLANS_PER_INVOCATION = 2

# Gemini 3.x strictly validates that functionCall parts in history carry the
# thought_signature the model originally returned; a synthesized call has none
# and the next model request fails with 400 INVALID_ARGUMENT. The API documents
# this exact bypass token for injected function calls
# (https://ai.google.dev/gemini-api/docs/thought-signatures). The SDK field is
# raw bytes serialized as base64url, so store the decoded bytes — they re-encode
# to the literal token on the wire.
_INJECTED_CALL_THOUGHT_SIGNATURE = base64.urlsafe_b64decode("context_engineering_is_the_way_to_go")


def policy_replan(denial_reason: str, final_attempt: bool = False) -> dict:
    """Relay a semantic-governance denial back to the model as tool feedback.

    Args:
        denial_reason: The policy judge's rationale for denying the proposed
            action, as extracted from the gateway's synthesized refusal.
        final_attempt: True when the per-invocation re-plan budget is spent —
            the model must wrap up instead of retrying the denied action.

    Returns:
        Dictionary with the denial reason and re-planning instructions.
    """
    if final_attempt:
        instruction = (
            "Your previously proposed action was denied by a semantic governance "
            "policy and was NOT executed. Do NOT retry the denied action or any "
            "variant of it. Produce your final answer to the user now: present "
            "the results of the work already completed in this conversation, and "
            "clearly state which action was blocked by governance policy and why."
        )
    else:
        instruction = (
            "Your previously proposed action was denied by a semantic governance "
            "policy and was NOT executed. Re-read the denial reason and continue "
            "helping the user with a compliant approach: scope tool calls to "
            "exactly what the user asked for, use only data belonging to the "
            "active applicant, and do not call tools unrelated to the current "
            "request. If part of the request cannot be completed compliantly, "
            "complete the rest and tell the user which part was blocked and why."
        )
    return {"policy_denial_reason": denial_reason, "instruction": instruction}


def _handle_sgp_denial(callback_context: CallbackContext, llm_response: LlmResponse) -> LlmResponse | None:
    """after_model_callback: convert SGP refusals into a policy_replan round trip."""
    if getattr(llm_response, "partial", False) or llm_response.content is None:
        return None
    parts = llm_response.content.parts or []
    # Only intercept the pure-text synthesized-refusal shape; a response that
    # carries a function call is a genuine model plan, not an SGP replacement.
    if any(getattr(p, "function_call", None) for p in parts):
        return None
    text = "".join(p.text for p in parts if getattr(p, "text", None))
    if not text.strip().startswith(_SGP_DENIAL_PREFIX):
        return None

    tracker = dict(callback_context.state.get(_REPLAN_STATE_KEY, {}))
    invocation_id = getattr(callback_context, "invocation_id", None) or "unknown"
    if tracker.get("invocation_id") != invocation_id:
        tracker = {"invocation_id": invocation_id, "attempts": 0}
    if tracker["attempts"] >= _MAX_REPLANS_PER_INVOCATION:
        logger.warning(
            "SGP denial persists after %d re-plan attempt(s); letting the refusal stand.",
            tracker["attempts"],
        )
        return None
    tracker["attempts"] += 1
    callback_context.state[_REPLAN_STATE_KEY] = tracker

    reason = text.split("Reason:", 1)[-1].strip() if "Reason:" in text else text.strip()
    logger.info(
        "SGP denial intercepted (re-plan %d/%d): %s",
        tracker["attempts"],
        _MAX_REPLANS_PER_INVOCATION,
        reason[:200],
    )
    replan_part = genai_types.Part.from_function_call(
        name="policy_replan",
        args={
            "denial_reason": reason,
            "final_attempt": tracker["attempts"] >= _MAX_REPLANS_PER_INVOCATION,
        },
    )
    replan_part.thought_signature = _INJECTED_CALL_THOUGHT_SIGNATURE
    return LlmResponse(content=genai_types.Content(role="model", parts=[replan_part]))


# ── The agent ────────────────────────────────────────────────────────────────


class _PickleSafeAgent(Agent):
    """Agent that rebuilds with MCP tools when unpickled or deep-copied."""

    def __reduce__(self):
        return (_build_agent, ())

    def __deepcopy__(self, memo):
        return _build_agent()


def _build_agent():
    """Build the agent with utility tools plus discovered MCP toolsets.

    Called at import time for local dev, and at unpickle time on Agent Engine.
    """
    _tools: list = [
        tools.get_current_time,
        tools.list_mcp_connections,
        policy_replan,
    ]
    _tools.extend(_discover_mcp_toolsets())

    instruction = _INSTRUCTION_TEMPLATE.format(mcp_services_doc=_render_mcp_services_doc())

    return _PickleSafeAgent(
        model=os.environ.get("MODEL_NAME", "gemini-3.5-flash"),
        name="mortgage_assistant_agent",
        description=(
            "A mortgage underwriting assistant that connects to legacy document management, "
            "income verification, and corporate email systems through an Agent Gateway."
        ),
        instruction=instruction,
        tools=_tools,
        on_tool_error_callback=_handle_tool_error,
        after_model_callback=_handle_sgp_denial,
    )


root_agent = _build_agent()
