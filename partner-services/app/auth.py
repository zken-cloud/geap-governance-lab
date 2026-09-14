"""Minimal OAuth2 server for the Cymbal Partner Services mock.

Scope: exactly what the Auth Manager demo needs -- client_credentials (2LO) and
authorization_code with optional PKCE (3LO). Tokens are stateless HS256 JWTs so
the service can scale past one instance without shared state.

THIS IS A DEMO MOCK, NOT A SECURITY REFERENCE. It is deliberately simple:
authorization codes are self-contained JWTs rather than one-time server-side
records, so a replayed code works until it expires. Do not lift this into
anything real.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

# Signing key for the mock's own tokens. Fixed by default so restarts don't
# invalidate live demo sessions; override in Cloud Run if you care.
SIGNING_KEY = os.environ.get("OAUTH_SIGNING_KEY", "cymbal-partner-services-demo-key").encode()

API_KEYS = {
    k.strip() for k in os.environ.get("CREDIT_BUREAU_API_KEYS", "cymbal-credit-key-alpha").split(",") if k.strip()
}

TWO_LO_CLIENT_ID = os.environ.get("TWO_LO_CLIENT_ID", "cymbal-insurance-client")
TWO_LO_CLIENT_SECRET = os.environ.get("TWO_LO_CLIENT_SECRET", "cymbal-insurance-secret")

THREE_LO_CLIENT_ID = os.environ.get("THREE_LO_CLIENT_ID", "cymbal-openbanking-client")
THREE_LO_CLIENT_SECRET = os.environ.get("THREE_LO_CLIENT_SECRET", "cymbal-openbanking-secret")

# Consent can be driven from either side, each with its own redirect URI:
#   Auth Manager -> https://agentidentitycredentials.googleapis.com/.../oauthcallback
#   Gemini Enterprise -> https://vertexaisearch.cloud.google.com/static/oauth/oauth.html
# Both are allowed by default so we are not blocked whichever path GE uses.
DEFAULT_REDIRECT_PREFIXES = (
    "https://agentidentitycredentials.googleapis.com/",
    "https://vertexaisearch.cloud.google.com/",
    "http://localhost",
)
REDIRECT_PREFIXES = tuple(
    p.strip()
    for p in os.environ.get("OAUTH_REDIRECT_PREFIXES", ",".join(DEFAULT_REDIRECT_PREFIXES)).split(",")
    if p.strip()
)

ACCESS_TOKEN_TTL = int(os.environ.get("ACCESS_TOKEN_TTL", "3600"))
AUTH_CODE_TTL = int(os.environ.get("AUTH_CODE_TTL", "300"))


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64u_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def mint(claims: dict, ttl: int) -> str:
    """Sign a compact JWT."""
    header = _b64u(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = dict(claims)
    body.setdefault("iat", int(time.time()))
    body["exp"] = int(time.time()) + ttl
    payload = _b64u(json.dumps(body, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    signature = _b64u(hmac.new(SIGNING_KEY, signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def verify(token: str) -> dict | None:
    """Return claims for a valid, unexpired token, else None."""
    try:
        header, payload, signature = token.split(".")
    except ValueError:
        return None

    expected = _b64u(hmac.new(SIGNING_KEY, f"{header}.{payload}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None

    try:
        claims = json.loads(_b64u_decode(payload))
    except (ValueError, json.JSONDecodeError):
        return None

    if claims.get("exp", 0) < time.time():
        return None
    return claims


def redirect_uri_allowed(redirect_uri: str) -> bool:
    return any(redirect_uri.startswith(prefix) for prefix in REDIRECT_PREFIXES)


def verify_pkce(code_challenge: str, method: str, code_verifier: str) -> bool:
    if not code_challenge:
        return True  # PKCE not used for this authorization
    if method == "plain":
        return hmac.compare_digest(code_challenge, code_verifier)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    return hmac.compare_digest(code_challenge, _b64u(digest))
