"""Tests for the pure parts of partner_tools: vault outcomes, header mapping, refusals."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agent import partner_tools as pt


def _ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.state = {}
    ctx.user_id = "julian"
    return ctx


PENDING = {"path": "/x", "params": {}, "scopes": ["read"]}


class TestVaultOutcome:
    def test_success_returns_none(self):
        assert pt._vault_outcome(_ctx(), "p", {"success": {"header": "X-API-Key", "token": "t"}}, PENDING) is None

    def test_consent_required_stashes_nonce_and_pending(self):
        ctx = _ctx()
        result = {"uriConsentRequired": {"authorizationUri": "https://consent", "consentNonce": "n1"}}
        out = pt._vault_outcome(ctx, "cymbal-openbanking", result, PENDING)
        assert out["status"] == "consent_required"
        assert out["consent_url"] == "https://consent"
        assert ctx.state[pt._nonce_key("cymbal-openbanking")] == "n1"
        assert ctx.state[pt._pending_key("cymbal-openbanking")] == PENDING

    @pytest.mark.parametrize(
        "result, status",
        [
            ({"consentRejected": {}}, "consent_rejected"),
            ({"pending": {}}, "pending"),
            ({"somethingNew": {}}, "credential_denied"),
        ],
    )
    def test_other_outcomes(self, result, status):
        out = pt._vault_outcome(_ctx(), "p", result, PENDING)
        assert out["status"] == status and out["auth_provider"] == "p"


class TestHeadersFromSuccess:
    def test_api_key_header_used_verbatim(self):
        assert pt._headers_from_success({"header": "X-API-Key", "token": "k"}) == {"X-API-Key": "k"}

    def test_bearer_header_split_into_name_and_prefix(self):
        assert pt._headers_from_success({"header": "Authorization: Bearer", "token": "t"}) == {
            "Authorization": "Bearer t"
        }

    def test_missing_token_is_an_error(self):
        with pytest.raises(RuntimeError):
            pt._headers_from_success({"header": "X-API-Key"})


class TestCall:
    @pytest.mark.asyncio
    async def test_vault_refusal_is_a_result_not_an_exception(self):
        with patch.object(pt, "_retrieve", AsyncMock(side_effect=pt.VaultRefused("HTTP 403: nope"))):
            out = await pt._call(_ctx(), "p", "/x", scopes=["read"])
        assert out["status"] == "credential_denied"
        assert out["scopes_requested"] == ["read"]
        assert "HTTP 403" in out["error"]

    @pytest.mark.asyncio
    async def test_unreachable_vault_is_reported_not_swallowed(self):
        with patch.object(pt, "_retrieve", AsyncMock(side_effect=httpx.ConnectError("down"))):
            out = await pt._call(_ctx(), "p", "/x")
        assert out["status"] == "error" and "vault" in out["message"]

    @pytest.mark.asyncio
    async def test_success_calls_partner_and_returns_data(self):
        success = {"success": {"header": "X-API-Key", "token": "k", "scopes": ["read"]}}
        with (
            patch.object(pt, "_retrieve", AsyncMock(return_value=success)),
            patch.object(pt, "_partner_request", AsyncMock(return_value=(200, {"score": 742}))) as req,
        ):
            out = await pt._call(_ctx(), "p", "/score", {"last_name": "Sterling"})
        assert out == {"status": "ok", "data": {"score": 742}, "scopes_granted": ["read"]}
        assert req.call_args.args[3] == {"X-API-Key": "k"}

    @pytest.mark.asyncio
    async def test_partner_refusal_carries_status_and_detail(self):
        success = {"success": {"header": "Authorization: Bearer", "token": "t"}}
        with (
            patch.object(pt, "_retrieve", AsyncMock(return_value=success)),
            patch.object(pt, "_partner_request", AsyncMock(return_value=(403, {"error": "wrong subject"}))),
        ):
            out = await pt._call(_ctx(), "p", "/x")
        assert out["status"] == "partner_refused"
        assert out["http_status"] == 403 and out["detail"] == {"error": "wrong subject"}
