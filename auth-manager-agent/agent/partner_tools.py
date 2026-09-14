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

"""Partner-system tools whose credentials come from Agent Identity Auth Manager.

Nothing in this file holds a secret. Each tool names an authProvider; the vault
decides whether to issue a credential and tells us which header to put it in.

WHY THIS TALKS REST INSTEAD OF USING ADK's GcpAuthProvider
----------------------------------------------------------
Two ADK paths were tried against the live deployment on 2026-08-06 and both
failed. Recorded here so nobody re-walks them:

1. ``OpenAPIToolset(spec_dict=..., auth_scheme=GcpAuthProviderScheme(...))``
   dies before any network call with::

       AttributeError: 'str' object has no attribute 'name'

   `ToolAuthHandler` builds its cache key as ``auth_scheme.type_.name``
   (tools/openapi_tool/openapi_spec_parser/tool_auth_handler.py:103), which
   assumes the `SecuritySchemeType` **enum** carried by OpenAPI schemes.
   `GcpAuthProviderScheme` extends `CustomAuthScheme`, whose ``type_`` is a
   plain ``str`` (auth/auth_schemes.py:53).

2. Calling ``GcpAuthProvider.get_auth_credential()`` directly gets further — the
   tool runs, the vault is reached — and is then rejected by the service::

       Invalid request. ... To match a binding the request message must have all
       the required fields initialized ... URI: "/v1alpha/{connector=projects/*/
       locations/*/connectors/*}/..."

   The pinned `google-cloud-iamconnectorcredentials` client still addresses the
   old ``connectors/*`` resource naming. The live API (both v1 and v1alpha,
   discovery revision 20260730) has moved to
   ``authProviders/*/credentials:retrieve``. The SDK lags the service.

So this module calls the REST endpoint directly, which is how the rest of this
project already talks to preview surfaces. Re-test both ADK paths when the SDK
next moves; if either starts working, this module can shrink.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

import google.auth
import google.auth.transport.requests
import httpx
from google.adk.tools.tool_context import ToolContext
from opentelemetry import trace

# Agent Engine already stands up a TracerProvider exporting to
# telemetry.googleapis.com, so this joins the invocation's existing trace.
#
# Without these spans Auth Manager is invisible in Cloud Trace: the credential
# calls go out over httpx, which carries no instrumentation, so a trace shows the
# gateway's SemanticGovernance spans and nothing about where the credential came
# from. That makes the vault impossible to demo from the trace view.
#
# NOTHING HERE MAY CARRY A TOKEN. Record the provider, the mode, the scopes and
# the outcome — never the credential itself, and never the Authorization header
# value. A trace is exported, retained and widely readable.
_tracer = trace.get_tracer("cymbal.partner_access.auth_manager")

logger = logging.getLogger(__name__)

PROJECT_ID = os.environ["GOOGLE_CLOUD_PROJECT"]  # set by Agent Runtime
AUTH_PROVIDER_LOCATION = os.environ.get("AUTH_PROVIDER_LOCATION", "us-central1")
# The Cymbal Partner Services URL. deploy.py sets it from --partner-base-url.
PARTNER_BASE_URL = os.environ["PARTNER_BASE_URL"].rstrip("/")
# Where Google's oauthcallback sends the user once they have consented.
#
# Not optional in practice: the 3LO providers reject retrieve outright with
#   HTTP 400 ... continue_uri: continue_uri must be present
# so without it the consent URL is never even issued.
#
# The landing page only ends the browser journey. Completing the handshake means
# calling credentials:finalize with the user_id_validation_state, consent_nonce
# and uuid that Google appends to this URI — which needs a client that can read
# its own query string. Gemini Enterprise is that client (phase 4); a REST-driven
# invocation is not, so over REST the flow stops at `consent_required` by design.
# deploy.py sets it from --continue-uri.
CONTINUE_URI = os.environ.get("AUTH_MANAGER_CONTINUE_URI") or None

CREDENTIALS_API = "https://agentidentitycredentials.googleapis.com/v1"
_HTTP_TIMEOUT = 30.0

_credentials_cache: Any = None


class VaultRefused(RuntimeError):
    """Auth Manager answered, and the answer was no (any non-200 from credentials:retrieve)."""


def _agent_token() -> str:
    """An access token for *this agent's own identity*.

    Inside Agent Engine with AGENT_IDENTITY enabled, ADC resolves to the agent's
    SPIFFE principal — the same principal named in each provider's IAM policy.
    That is what makes the vault's authorization decision about the agent rather
    than about any human.
    """
    global _credentials_cache
    if _credentials_cache is None:
        _credentials_cache, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    if not _credentials_cache.valid:
        _credentials_cache.refresh(google.auth.transport.requests.Request())
    return _credentials_cache.token


def _provider_path(name: str) -> str:
    return f"projects/{PROJECT_ID}/locations/{AUTH_PROVIDER_LOCATION}/authProviders/{name}"


async def _retrieve(provider: str, user_id: str, scopes: list[str] | None) -> dict[str, Any]:
    """POST credentials:retrieve. Returns the raw oneof response."""
    url = f"{CREDENTIALS_API}/{_provider_path(provider)}/credentials:retrieve"
    body: dict[str, Any] = {"userId": user_id}
    if scopes:
        body["scopes"] = scopes
    if CONTINUE_URI:
        body["continueUri"] = CONTINUE_URI

    with _tracer.start_as_current_span("auth_manager.retrieve_credential") as span:
        span.set_attribute("auth_manager.provider", provider)
        span.set_attribute("auth_manager.user_id", user_id)
        span.set_attribute("auth_manager.scopes_requested", scopes or [])

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {_agent_token()}",
                    "Content-Type": "application/json",
                    "x-goog-user-project": PROJECT_ID,
                },
            )

        span.set_attribute("auth_manager.http_status", response.status_code)
        if response.status_code != 200:
            try:
                detail = response.json().get("error", {}).get("message", response.text[:400])
            except Exception:
                detail = response.text[:400]
            # A refusal is the interesting case — make it findable in the trace.
            span.set_attribute("auth_manager.outcome", "DENIED")
            span.set_attribute("auth_manager.denial_reason", detail[:400])
            raise VaultRefused(f"HTTP {response.status_code}: {detail}")

        result = response.json()
        outcome = next(
            (k for k in ("success", "uriConsentRequired", "consentRejected", "pending") if k in result),
            "unknown",
        )
        span.set_attribute("auth_manager.outcome", outcome)
        if outcome == "success":
            success = result["success"]
            # The header NAME is the demo point: the vault dictates it. The
            # header VALUE is the credential and must never be recorded.
            span.set_attribute("auth_manager.header_name", (success.get("header") or "").split(":")[0])
            span.set_attribute("auth_manager.scopes_granted", success.get("scopes", []))
        return result


def _headers_from_success(success: dict[str, Any]) -> dict[str, str]:
    """Build request headers from the vault's Success message.

    The vault dictates the header name; it is not ours to choose. API-key
    providers return a custom header such as ``X-API-Key``; OAuth providers
    return ``Authorization: Bearer`` — a name *and* a prefix, so it is split
    rather than used verbatim.
    """
    header = success.get("header") or ""
    token = success.get("token") or ""
    if not header or not token:
        raise RuntimeError("Auth Manager returned a credential with no header or no token.")

    name, _, prefix = header.partition(":")
    name = name.strip()
    prefix = prefix.strip()
    return {name: f"{prefix} {token}" if prefix else token}


def _refusal(status: str, provider: str, message: str, **detail: Any) -> dict[str, Any]:
    """A refusal is a result, not an exception: the model explains it, it does not retry it."""
    return {"status": status, "auth_provider": provider, "message": message, **detail}


def _vault_outcome(
    tool_context: ToolContext,
    provider: str,
    result: dict[str, Any],
    pending: dict[str, Any],
) -> dict[str, Any] | None:
    """Translate a non-success credentials:retrieve response into a tool result.

    Returns None when the vault issued a credential (`success`), so the caller
    can proceed to the partner system.
    """
    if "uriConsentRequired" in result:
        consent = result["uriConsentRequired"]
        # credentials:finalize needs the nonce later, and it is only ever handed
        # out here — Google's redirect carries the validation state but NOT the
        # nonce. Lose it and the user has to start consent again.
        tool_context.state[_nonce_key(provider)] = consent.get("consentNonce")
        # Remember what the user asked for, so finalize can replay it.
        tool_context.state[_pending_key(provider)] = pending
        return _refusal(
            "consent_required",
            provider,
            "The applicant has not yet granted access to this system. They must "
            "approve it themselves — this agent cannot consent on their behalf.",
            consent_url=consent.get("authorizationUri"),
            scopes_requested=pending["scopes"],
        )
    if "consentRejected" in result:
        return _refusal("consent_rejected", provider, "The applicant declined to grant access to this system.")
    if "pending" in result:
        return _refusal("pending", provider, "The credential is not ready yet. Ask the user to retry shortly.")
    if "success" not in result:
        return _refusal(
            "credential_denied", provider, "Unrecognised response from the credential vault.", error=str(list(result))
        )
    return None


async def _partner_request(
    provider: str, path: str, params: dict[str, Any] | None, headers: dict[str, str]
) -> tuple[int, Any]:
    """GET the partner endpoint with the vaulted credential; returns (status, body)."""
    with _tracer.start_as_current_span("partner_api.call") as span:
        span.set_attribute("partner_api.path", path)
        span.set_attribute("auth_manager.provider", provider)
        # Which header the credential travelled in — not its value.
        span.set_attribute("partner_api.auth_header", next(iter(headers), ""))
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.get(f"{PARTNER_BASE_URL}{path}", params=params or {}, headers=headers)
        span.set_attribute("partner_api.http_status", response.status_code)
        span.set_attribute("partner_api.outcome", "OK" if response.status_code == 200 else "REFUSED")
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, response.text[:500]


async def _call(
    tool_context: ToolContext,
    provider: str,
    path: str,
    params: dict[str, Any] | None = None,
    scopes: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch a vaulted credential, then call the partner endpoint with it."""
    user_id = getattr(tool_context, "user_id", None) or "unknown-user"
    try:
        result = await _retrieve(provider, user_id, scopes)
    except VaultRefused as e:
        return _refusal(
            "credential_denied",
            provider,
            "Auth Manager did not issue a credential for this call. This is a policy "
            "decision at the credential vault, taken before the partner system was contacted.",
            scopes_requested=scopes or [],
            error=str(e),
        )
    except httpx.HTTPError as e:
        return _refusal("error", provider, f"Could not reach the credential vault: {e}")

    pending = {"path": path, "params": params or {}, "scopes": scopes or []}
    if refusal := _vault_outcome(tool_context, provider, result, pending):
        return refusal
    success = result["success"]
    try:
        headers = _headers_from_success(success)
    except RuntimeError as e:
        return _refusal("credential_denied", provider, str(e))

    try:
        status, body = await _partner_request(provider, path, params, headers)
    except httpx.HTTPError as e:
        return _refusal("error", provider, f"Could not reach the partner system: {e}")
    if status == 200:
        # Scopes actually granted can be narrower than those requested — the
        # applicant may have declined some at the consent screen.
        return {"status": "ok", "data": body, "scopes_granted": success.get("scopes", [])}
    # The vault issued a credential and the partner still refused it — wrong
    # subject, or a scope the token does not carry.
    return _refusal(
        "partner_refused",
        provider,
        "The partner system rejected the vaulted credential. Typically this means "
        "the token is bound to a different applicant, or lacks the required scope.",
        http_status=status,
        detail=body,
    )


def _nonce_key(provider: str) -> str:
    return f"_consent_nonce_{provider}"


def _pending_key(provider: str) -> str:
    return f"_pending_call_{provider}"


async def _finalize(provider: str, user_id: str, validation_state: str, nonce: str) -> dict[str, Any]:
    """POST credentials:finalize — the step that actually banks the token.

    Consenting at the third party is not enough. Until this call lands, the vault
    holds nothing and every retrieve keeps returning uriConsentRequired, which
    looks exactly like the user never consented at all.
    """
    url = f"{CREDENTIALS_API}/{_provider_path(provider)}/credentials:finalize"
    with _tracer.start_as_current_span("auth_manager.finalize_consent") as span:
        span.set_attribute("auth_manager.provider", provider)
        span.set_attribute("auth_manager.user_id", user_id)

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.post(
                url,
                json={
                    "userId": user_id,
                    "userIdValidationState": validation_state,
                    "consentNonce": nonce,
                },
                headers={
                    "Authorization": f"Bearer {_agent_token()}",
                    "Content-Type": "application/json",
                    "x-goog-user-project": PROJECT_ID,
                },
            )

        span.set_attribute("auth_manager.http_status", response.status_code)
        if response.status_code != 200:
            try:
                detail = response.json().get("error", {}).get("message", response.text[:400])
            except Exception:
                detail = response.text[:400]
            span.set_attribute("auth_manager.outcome", "FAILED")
            span.set_attribute("auth_manager.denial_reason", detail[:400])
            raise RuntimeError(f"HTTP {response.status_code}: {detail}")
        span.set_attribute("auth_manager.outcome", "CONSENT_REGISTERED")
        return response.json()


# ── Tools ─────────────────────────────────────────────────────────────────────
# Tool names are load-bearing: SGP policies bind to them.


async def get_credit_score(first_name: str, last_name: str, tool_context: ToolContext) -> dict:
    """Retrieve an applicant's credit score from the Cymbal credit bureau.

    Uses an API key held in Agent Identity Auth Manager. A machine credential —
    no user consent is involved. Only call this for the applicant the current
    request is about.

    Args:
        first_name: Applicant's first name, e.g. 'Julian'.
        last_name: Applicant's last name, e.g. 'Sterling'.

    Returns:
        The credit score and reporting detail, or a structured refusal.
    """
    return await _call(
        tool_context,
        "cymbal-credit-bureau",
        "/credit-bureau/v1/score",
        {"first_name": first_name, "last_name": last_name},
    )


async def get_insurance_quote(property_value_usd: str, address: str, tool_context: ToolContext) -> dict:
    """Get an indicative homeowner's insurance quote for a property.

    Uses two-legged OAuth: the agent acts as itself. The quote is for a property,
    not a person, so no user consent is involved.

    Args:
        property_value_usd: Property value in USD, digits only, e.g. '650000'.
        address: Property address.

    Returns:
        The indicative annual premium, or a structured refusal.
    """
    return await _call(
        tool_context,
        "cymbal-insurance",
        "/insurance/v1/quote",
        {"property_value_usd": property_value_usd, "address": address},
        scopes=["insurance.quote"],
    )


async def list_bank_accounts(tool_context: ToolContext) -> dict:
    """List the consenting applicant's bank accounts.

    Uses three-legged OAuth. The token belongs to the applicant who consented and
    to nobody else — there is deliberately no parameter for choosing whose
    accounts to read. If the applicant has not consented, this returns a consent
    link to hand to the user.

    Returns:
        The consenting applicant's accounts, a consent request, or a refusal.
    """
    return await _call(
        tool_context,
        "cymbal-openbanking",
        "/openbanking/v1/accounts",
        scopes=["openbanking.accounts.read"],
    )


async def verify_bank_income(tool_context: ToolContext) -> dict:
    """Verify the consenting applicant's income from their bank records.

    Uses three-legged OAuth, bound to the applicant who consented. An independent
    cross-check against the income stated on a tax return.

    Returns:
        Bank-derived annual income, a consent request, or a refusal.
    """
    return await _call(
        tool_context,
        "cymbal-openbanking",
        "/openbanking/v1/income",
        scopes=["openbanking.income.verify"],
    )


# NOTE (not visible to the model — deliberately kept out of the docstring):
# the `cymbal-openbanking` provider's allowedScopes are accounts.read and
# income.verify only. `openbanking.transactions.read` is absent, so Auth Manager
# rejects this at the vault, before any consent screen and before the partner is
# contacted. That is the scope-governance demo beat.
#
# The docstring below therefore reads like any other tool. An earlier version
# spelled out "expect credential_denied", and the SGP judge — which is given the
# tool descriptions — read that and denied the call itself, pre-empting the vault
# and demoing the wrong control. Describe intent; let the vault do the refusing.
async def list_bank_transactions(account_id: str, tool_context: ToolContext) -> dict:
    """List transactions for one of the consenting applicant's accounts.

    Uses three-legged OAuth, bound to the applicant who consented. Use this when
    the user asks to see account activity or individual transactions.

    Args:
        account_id: Account identifier from list_bank_accounts.

    Returns:
        The account's transactions, or a structured refusal.
    """
    return await _call(
        tool_context,
        "cymbal-openbanking",
        "/openbanking/v1/transactions",
        {"account_id": account_id},
        scopes=["openbanking.transactions.read"],
    )


async def complete_bank_consent(redirect_url: str, tool_context: ToolContext) -> dict:
    """Finish linking the applicant's bank account after they approved access.

    Call this once the user says they have completed the consent screen and has
    given you the URL they were redirected to. Consent at the bank alone does not
    grant access — this step is what stores the approval.

    Args:
        redirect_url: The full URL the user landed on after approving access,
            including everything after the '?'. It contains a
            'user_id_validation_state' value.

    Returns:
        Confirmation that access is linked, or a structured refusal.
    """
    user_id = getattr(tool_context, "user_id", None) or "unknown-user"
    provider = "cymbal-openbanking"

    parsed = parse_qs(urlparse(redirect_url.strip()).query)
    validation_state = (parsed.get("user_id_validation_state") or [""])[0]
    if not validation_state:
        return {
            "status": "error",
            "message": (
                "That URL has no 'user_id_validation_state' parameter, so the consent "
                "cannot be completed. Ask the user for the full address bar contents of "
                "the page they landed on after approving access."
            ),
        }

    nonce = tool_context.state.get(_nonce_key(provider))
    if not nonce:
        return {
            "status": "error",
            "message": (
                "No pending consent was found for this conversation. The nonce is only "
                "issued when consent is requested and is not carried in the redirect, so "
                "a consent started in a different conversation cannot be completed here. "
                "Start the bank linking again in this conversation."
            ),
        }

    try:
        await _finalize(provider, user_id, validation_state, nonce)
    except Exception as e:
        return {
            "status": "consent_finalize_failed",
            "auth_provider": provider,
            "error": str(e),
            "message": (
                "The bank approval could not be registered. The link may have already "
                "been used or expired — consent links are single-use."
            ),
        }

    # ADK's State supports only get/set — no pop and no del (sessions/state.py).
    # Clearing means assigning None.
    pending = tool_context.state.get(_pending_key(provider))
    tool_context.state[_nonce_key(provider)] = None
    tool_context.state[_pending_key(provider)] = None

    if not pending:
        return {
            "status": "ok",
            "message": "Bank access is now linked. Retry whichever open banking tool you need.",
        }

    # Replay the call that triggered consent, so the user gets what they
    # originally asked for without repeating themselves.
    result = await _call(
        tool_context,
        provider,
        pending["path"],
        pending["params"] or None,
        pending["scopes"] or None,
    )
    return {
        "status": "ok",
        "message": (
            "Bank access is now linked, and the request that needed it has been "
            "completed. Present the result below to the user directly — do not ask "
            "them to repeat their original request."
        ),
        "result": result,
    }
