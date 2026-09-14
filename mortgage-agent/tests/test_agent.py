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

"""Tests for mortgage agent error handling logic."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from agent import agent as agent_module
from agent.agent import (
    _discover_mcp_toolsets,
    _find_http_status_error,
    _handle_tool_error,
    _render_mcp_services_doc,
)


def _make_http_status_error(status_code: int) -> httpx.HTTPStatusError:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    return httpx.HTTPStatusError("error", request=MagicMock(), response=response)


class TestFindHttpStatusError:
    def test_direct_match(self):
        exc = _make_http_status_error(403)
        assert _find_http_status_error(exc, 403) is True

    def test_wrong_status_code(self):
        exc = _make_http_status_error(500)
        assert _find_http_status_error(exc, 403) is False

    def test_chained_via_cause(self):
        inner = _make_http_status_error(403)
        outer = RuntimeError("wrapper")
        outer.__cause__ = inner
        assert _find_http_status_error(outer, 403) is True

    def test_chained_via_context(self):
        inner = _make_http_status_error(403)
        outer = RuntimeError("wrapper")
        outer.__context__ = inner
        assert _find_http_status_error(outer, 403) is True

    def test_exception_group(self):
        inner = _make_http_status_error(403)
        group = BaseExceptionGroup("group", [RuntimeError("other"), inner])
        assert _find_http_status_error(group, 403) is True

    def test_no_match(self):
        exc = RuntimeError("unrelated")
        assert _find_http_status_error(exc, 403) is False

    def test_nested_exception_group(self):
        inner = _make_http_status_error(403)
        inner_group = BaseExceptionGroup("inner", [inner])
        outer_group = BaseExceptionGroup("outer", [inner_group])
        assert _find_http_status_error(outer_group, 403) is True


def _make_tool_context(state: dict | None = None) -> MagicMock:
    """ToolContext mock whose `.state` is a real dict so .get/__setitem__ work."""
    ctx = MagicMock()
    ctx.state = {} if state is None else state
    return ctx


class TestHandleToolError:
    def test_403_returns_block_message(self):
        tool = MagicMock()
        tool.name = "send_email"
        error = _make_http_status_error(403)
        result = _handle_tool_error(tool, {}, _make_tool_context(), error)
        assert result is not None
        assert "send_email" in result["error"]
        assert "authorization policies" in result["error"]
        # No retries: the very first 403 is the hard-stop message.
        assert "Do not call this tool again" in result["error"]

    def test_non_403_returns_none(self):
        tool = MagicMock()
        tool.name = "read_email"
        error = _make_http_status_error(500)
        result = _handle_tool_error(tool, {}, _make_tool_context(), error)
        assert result is None

    def test_non_http_error_returns_none(self):
        tool = MagicMock()
        tool.name = "read_email"
        error = RuntimeError("something broke")
        result = _handle_tool_error(tool, {}, _make_tool_context(), error)
        assert result is None

    def test_403_wrapped_in_exception_group(self):
        # ADK's MCP client surfaces tool failures inside a TaskGroup.
        tool = MagicMock()
        tool.name = "send_email"
        error = BaseExceptionGroup("group", [RuntimeError("other"), _make_http_status_error(403)])
        result = _handle_tool_error(tool, {}, _make_tool_context(), error)
        assert result is not None and "send_email" in result["error"]


class TestInstructionRendering:
    """The instruction must enumerate live registry tool names, not just prefixes,
    so the LLM has no room to invent plausible-but-wrong tool names."""

    _DISCOVERED = [
        {
            "name": "legacy-dms",
            "tool_name_prefix": "legacy_dms",
            "tools": ["search_documents", "get_document"],
        },
        {
            "name": "corporate-email",
            "tool_name_prefix": "corporate_email",
            "tools": ["list_messages", "get_message"],
        },
        {
            "name": "income-verification",
            "tool_name_prefix": "income_verification",
            "tools": ["verify_income"],
        },
    ]

    def test_render_includes_live_tool_names_and_descriptions(self):
        with patch.object(agent_module, "DISCOVERED_MCP_SERVERS", self._DISCOVERED):
            doc = _render_mcp_services_doc()
        # Concrete prefixed names appear.
        assert "`legacy_dms_search_documents`" in doc
        assert "`legacy_dms_get_document`" in doc
        assert "`corporate_email_list_messages`" in doc
        assert "`corporate_email_get_message`" in doc
        assert "`income_verification_verify_income`" in doc
        # The wildcard form must NOT appear when tools are known — that
        # wildcard is what gave the LLM rope to invent names.
        assert "`legacy_dms_*`" not in doc
        assert "`corporate_email_*`" not in doc
        assert "`income_verification_*`" not in doc
        # Descriptions for known services come through.
        assert "legacy document management system" in doc
        assert "corporate communications system" in doc
        assert "third-party income verification vendor" in doc

    def test_render_handles_empty_discovery(self):
        with patch.object(agent_module, "DISCOVERED_MCP_SERVERS", []):
            doc = _render_mcp_services_doc()
        assert "no MCP services discovered" in doc

    def test_render_handles_unknown_service_with_tools(self):
        with patch.object(
            agent_module,
            "DISCOVERED_MCP_SERVERS",
            [
                {
                    "name": "future-service",
                    "tool_name_prefix": "future_service",
                    "tools": ["do_thing"],
                }
            ],
        ):
            doc = _render_mcp_services_doc()
        assert "**future-service** (tools: `future_service_do_thing`)" in doc
        # No description prose appended for unknown services.
        assert "connects to" not in doc

    def test_render_falls_back_to_wildcard_when_tools_empty(self):
        with patch.object(
            agent_module,
            "DISCOVERED_MCP_SERVERS",
            [{"name": "future-service", "tool_name_prefix": "future_service", "tools": []}],
        ):
            doc = _render_mcp_services_doc()
        assert "**future-service** (tools prefixed `future_service_*`)" in doc

    def test_render_handles_service_with_no_tools_field(self):
        # An entry that has neither `tools` key set nor a populated list —
        # `entry.get("tools") or []` must coerce both to the same fallback.
        with patch.object(
            agent_module,
            "DISCOVERED_MCP_SERVERS",
            [{"name": "future-service", "tool_name_prefix": "future_service"}],
        ):
            doc = _render_mcp_services_doc()
        assert "**future-service** (tools prefixed `future_service_*`)" in doc

    def test_render_handles_service_with_no_prefix_and_no_tools(self):
        with patch.object(
            agent_module,
            "DISCOVERED_MCP_SERVERS",
            [{"name": "broken-service"}],
        ):
            doc = _render_mcp_services_doc()
        assert "**broken-service** (no tools advertised)" in doc

    def test_built_agent_instruction_contains_live_tool_names_and_guardrails(self):
        with (
            patch.object(agent_module, "_discover_mcp_toolsets", return_value=[]),
            patch.object(agent_module, "DISCOVERED_MCP_SERVERS", self._DISCOVERED),
        ):
            built = agent_module._build_agent()
        instruction = built.instruction
        # Concrete prefixed names present.
        assert "`legacy_dms_search_documents`" in instruction
        assert "`legacy_dms_get_document`" in instruction
        assert "`income_verification_verify_income`" in instruction
        # Wildcard form must NOT leak through when tools are known.
        assert "`legacy_dms_*`" not in instruction
        assert "`corporate_email_*`" not in instruction
        assert "`income_verification_*`" not in instruction
        # Anti-hallucination guardrails are in place.
        assert "Only call tools by the exact names listed below." in instruction
        assert "never use a colon (`:`)" in instruction
        assert "Never invent tool names." in instruction


class TestRegistryDiscovery:
    """Toolsets come from the registry list, one per server, each signing its
    calls with this agent's own ID token for that server's origin."""

    def test_discover_builds_toolsets_with_agent_id_token_headers(self, monkeypatch):
        monkeypatch.setenv("MCP_REGISTRY_PROJECT", "test-project")
        monkeypatch.setenv("MCP_REGISTRY_LOCATION", "us-central1")
        monkeypatch.delenv("MCP_REGISTRY_FILTER", raising=False)

        registry_instance = MagicMock()
        registry_instance.list_mcp_servers.return_value = {
            "mcpServers": [
                {
                    "name": "projects/p/locations/l/mcpServers/x",
                    "mcpServerId": "x-id",
                    "displayName": "legacy-dms",
                    "interfaces": [{"protocolBinding": "JSON_RPC", "url": "https://x.example/mcp"}],
                    "tools": [{"name": "do_thing"}],
                }
            ]
        }
        toolset_cls = MagicMock()
        agent_module._DISCOVERY_CACHE = None

        with (
            patch(
                "google.adk.integrations.agent_registry.agent_registry.AgentRegistry", return_value=registry_instance
            ),
            patch("google.adk.integrations.agent_registry.agent_registry.AgentRegistrySingleMcpToolset", toolset_cls),
            patch("google.oauth2.id_token.fetch_id_token", return_value="h.eyJleHAiOjQxMDI0NDQ4MDB9.s") as fetch,
        ):
            result = _discover_mcp_toolsets()
            kwargs = toolset_cls.call_args.kwargs
            # The header provider signs with the agent's ID token for the server origin.
            headers = kwargs["header_provider"](MagicMock())

        assert result == [toolset_cls.return_value]
        assert kwargs["tool_name_prefix"] == "legacy_dms"
        assert kwargs["destination_resource_id"] == "x-id"
        assert kwargs["connection_params"].url == "https://x.example/mcp"
        # ADK's default 5 s connection timeout is left alone so denied calls fail fast.
        assert kwargs["connection_params"].timeout == 5.0
        assert headers == {"Authorization": "Bearer h.eyJleHAiOjQxMDI0NDQ4MDB9.s"}
        assert fetch.call_args.args[1] == "https://x.example"
        # Unprefixed tool names land in DISCOVERED_MCP_SERVERS for the instruction.
        assert agent_module.DISCOVERED_MCP_SERVERS[0]["tools"] == ["do_thing"]
        assert agent_module.DISCOVERED_MCP_SERVERS[0]["tool_name_prefix"] == "legacy_dms"

    def test_id_token_cached_until_near_expiry(self):
        import base64
        import json
        import time

        def jwt(exp):
            body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
            return f"h.{body}.s"

        fresh, stale = jwt(time.time() + 3600), jwt(time.time() + 30)
        with patch("google.oauth2.id_token.fetch_id_token", return_value=fresh) as fetch:
            tok = agent_module._AgentIDToken("https://svc.example")
            assert tok() == fresh and tok() == fresh
            assert fetch.call_count == 1
        with patch("google.oauth2.id_token.fetch_id_token", side_effect=[stale, fresh]) as fetch:
            tok = agent_module._AgentIDToken("https://svc.example")
            tok()
            assert tok() == fresh
            assert fetch.call_count == 2


class TestHandleSgpDenial:
    _REFUSAL = (
        "I am sorry, but I cannot proceed with that request. "
        "Reason: The suggested action queries for 3 years, which is more than requested."
    )

    def _make_context(self, invocation_id="inv-1", state=None):
        ctx = MagicMock()
        ctx.invocation_id = invocation_id
        ctx.state = state if state is not None else {}
        return ctx

    def _make_response(self, text=None, partial=False, function_call=None):
        from google.genai import types as genai_types

        parts = []
        if text is not None:
            parts.append(genai_types.Part(text=text))
        if function_call is not None:
            parts.append(genai_types.Part.from_function_call(name=function_call, args={}))
        response = MagicMock()
        response.partial = partial
        response.content = genai_types.Content(role="model", parts=parts)
        return response

    def test_refusal_becomes_policy_replan_call(self):
        from agent.agent import _handle_sgp_denial

        ctx = self._make_context()
        result = _handle_sgp_denial(ctx, self._make_response(text=self._REFUSAL))
        assert result is not None
        call = result.content.parts[0].function_call
        assert call.name == "policy_replan"
        assert call.args["denial_reason"].startswith("The suggested action")
        assert ctx.state["_sgp_replan"]["attempts"] == 1

    def test_normal_text_untouched(self):
        from agent.agent import _handle_sgp_denial

        ctx = self._make_context()
        assert _handle_sgp_denial(ctx, self._make_response(text="Here is your summary.")) is None
        assert "_sgp_replan" not in ctx.state

    def test_partial_chunks_skipped(self):
        from agent.agent import _handle_sgp_denial

        ctx = self._make_context()
        assert _handle_sgp_denial(ctx, self._make_response(text=self._REFUSAL, partial=True)) is None

    def test_function_call_response_untouched(self):
        from agent.agent import _handle_sgp_denial

        ctx = self._make_context()
        response = self._make_response(text=self._REFUSAL, function_call="legacy_dms_search_documents")
        assert _handle_sgp_denial(ctx, response) is None

    def test_replan_cap_per_invocation(self):
        from agent.agent import _MAX_REPLANS_PER_INVOCATION, _handle_sgp_denial

        ctx = self._make_context()
        for _ in range(_MAX_REPLANS_PER_INVOCATION):
            assert _handle_sgp_denial(ctx, self._make_response(text=self._REFUSAL)) is not None
        # Cap reached: the refusal is allowed to stand.
        assert _handle_sgp_denial(ctx, self._make_response(text=self._REFUSAL)) is None

    def test_cap_resets_on_new_invocation(self):
        from agent.agent import _MAX_REPLANS_PER_INVOCATION, _handle_sgp_denial

        ctx = self._make_context(invocation_id="inv-1")
        for _ in range(_MAX_REPLANS_PER_INVOCATION):
            _handle_sgp_denial(ctx, self._make_response(text=self._REFUSAL))
        ctx.invocation_id = "inv-2"
        assert _handle_sgp_denial(ctx, self._make_response(text=self._REFUSAL)) is not None

    def test_policy_replan_tool_payload(self):
        from agent.agent import policy_replan

        result = policy_replan("too broad")
        assert result["policy_denial_reason"] == "too broad"
        assert "compliant" in result["instruction"]

    def test_replan_call_carries_injected_thought_signature(self):
        # Gemini 3.x rejects history containing functionCall parts without a
        # thought_signature (400 INVALID_ARGUMENT); injected calls must carry
        # the documented bypass token.
        from agent.agent import _INJECTED_CALL_THOUGHT_SIGNATURE, _handle_sgp_denial

        ctx = self._make_context()
        result = _handle_sgp_denial(ctx, self._make_response(text=self._REFUSAL))
        assert result.content.parts[0].thought_signature == _INJECTED_CALL_THOUGHT_SIGNATURE

    def test_final_replan_flags_final_attempt(self):
        from agent.agent import _MAX_REPLANS_PER_INVOCATION, _handle_sgp_denial

        ctx = self._make_context()
        results = [
            _handle_sgp_denial(ctx, self._make_response(text=self._REFUSAL)) for _ in range(_MAX_REPLANS_PER_INVOCATION)
        ]
        assert results[0].content.parts[0].function_call.args["final_attempt"] is False
        assert results[-1].content.parts[0].function_call.args["final_attempt"] is True

    def test_policy_replan_final_attempt_instruction(self):
        from agent.agent import policy_replan

        assert "Do NOT retry" in policy_replan("x", final_attempt=True)["instruction"]
        assert "Do NOT retry" not in policy_replan("x")["instruction"]
