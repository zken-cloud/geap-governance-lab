"""Cymbal Partner Services - a synthetic SaaS for the Auth Manager demo.

Three mock partner systems, one per Auth Manager credential mode, plus the
OAuth2 server that backs two of them:

    /credit-bureau/v1/score        X-GOOG-API-KEY          API-key mode
    /insurance/v1/quote            Bearer (client creds)   2LO
    /openbanking/v1/*              Bearer (user consent)   3LO

Everything is synthetic. See app/data.py for the fixtures and app/auth.py for
the deliberately-minimal OAuth2 implementation.
"""

from __future__ import annotations

import base64
import html
import os
import urllib.parse

from fastapi import FastAPI, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import auth
import data

app = FastAPI(
    title="Cymbal Partner Services",
    description="Synthetic partner APIs for the GEAP Auth Manager demo. All data is invented.",
    version="1.0.0",
)


# -- helpers --------------------------------------------------------------

def _require_api_key(*candidates: str | None) -> None:
    """API-key mode. Rotating the key in the vault must break this.

    Auth Manager decides the header name, and for API-key providers it returns
    `header: "X-API-Key"` -- not the X-GOOG-API-KEY the original design assumed.
    Verified against credentials:retrieve on 2026-07-28. X-GOOG-API-KEY stays
    accepted as an alias so hand-rolled curl calls in the runbook still work.
    """
    if not any(c and c in auth.API_KEYS for c in candidates):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API key. Send it as X-API-Key (the header Auth Manager injects).",
        )


def _require_bearer(header: str | None, *, need_scope: str, subject_required: bool) -> dict:
    """Shared bearer check for the 2LO and 3LO endpoints."""
    if not header or not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token.")

    claims = auth.verify(header.split(" ", 1)[1].strip())
    if claims is None:
        raise HTTPException(status_code=401, detail="Token is invalid or expired.")

    granted = set(claims.get("scope", "").split())
    if need_scope not in granted:
        raise HTTPException(
            status_code=403,
            detail=f"Token lacks the '{need_scope}' scope. Granted: {sorted(granted) or 'none'}.",
        )

    # A 3LO token carries the consenting end user; a 2LO token never does.
    if subject_required and not claims.get("sub"):
        raise HTTPException(
            status_code=403,
            detail="This endpoint needs a user-delegated token; got a client-credentials token.",
        )
    return claims


# -- landing + health -----------------------------------------------------

@app.get("/healthz", include_in_schema=False)
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> str:
    return """<!doctype html><meta charset=utf-8>
<title>Cymbal Partner Services</title>
<style>
 body{font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
      max-width:44rem;margin:3rem auto;padding:0 1.5rem;color:#1f2328}
 code{background:#f2f4f7;padding:.1em .35em;border-radius:4px;font-size:.9em}
 th,td{text-align:left;padding:.4rem .8rem .4rem 0;border-bottom:1px solid #e8ecf0;vertical-align:top}
 .note{background:#fdf6e3;border-left:3px solid #9a6700;padding:.7rem 1rem;border-radius:0 6px 6px 0}
</style>
<h1>Cymbal Partner Services</h1>
<p>A <strong>synthetic</strong> SaaS standing in for three third-party systems in the
GEAP governance demo. Each endpoint authenticates differently, one per Google Cloud
Agent Identity <em>Auth Manager</em> credential mode.</p>
<table>
<tr><th>Endpoint</th><th>Auth</th><th>Mode</th></tr>
<tr><td><code>/credit-bureau/v1/score</code></td><td><code>X-API-Key</code></td><td>API key</td></tr>
<tr><td><code>/insurance/v1/quote</code></td><td>Bearer, client credentials</td><td>2LO</td></tr>
<tr><td><code>/openbanking/v1/*</code></td><td>Bearer, user consent</td><td>3LO</td></tr>
</table>
<p>OAuth2 endpoints: <code>/oauth/authorize</code>, <code>/oauth/token</code>.
API docs at <a href="/docs">/docs</a>.</p>
<div class=note><strong>All data here is invented</strong> - fictional people,
accounts and credit files. The OAuth implementation is a demo mock, not a
security reference.</div>
"""


# -- credit bureau: API-key mode ------------------------------------------

@app.get("/credit-bureau/v1/score", tags=["credit-bureau"])
def credit_score(
    first_name: str = Query(..., description="Applicant first name"),
    last_name: str = Query(..., description="Applicant last name"),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    x_goog_api_key: str | None = Header(None, alias="X-GOOG-API-KEY"),
) -> dict:
    """Pull a synthetic credit report. Requires a vaulted API key."""
    _require_api_key(x_api_key, x_goog_api_key)

    report = data.CREDIT_REPORTS.get(f"{first_name} {last_name}".strip().lower())
    if not report:
        raise HTTPException(status_code=404, detail=f"No credit file for {first_name} {last_name}.")
    return report


# -- insurance: 2LO -------------------------------------------------------

@app.get("/insurance/v1/quote", tags=["insurance"])
def insurance_quote(
    property_value_usd: int = Query(..., gt=0, description="Estimated property value"),
    address: str = Query("", description="Property address"),
    authorization: str | None = Header(None),
) -> dict:
    """Homeowners quote. Requires a client-credentials token: the agent acts as itself."""
    _require_bearer(authorization, need_scope="insurance.quote", subject_required=False)
    return data.quote_for(property_value_usd, address)


# -- open banking: 3LO ----------------------------------------------------

@app.get("/openbanking/v1/accounts", tags=["open-banking"])
def accounts(authorization: str | None = Header(None)) -> dict:
    """Accounts belonging to the consenting user - and only that user."""
    claims = _require_bearer(authorization, need_scope="openbanking.accounts.read", subject_required=True)
    user = data.BANK_USERS.get(claims["sub"])
    if not user:
        raise HTTPException(status_code=404, detail="No banking profile for the consenting user.")

    return {
        "user": user["display_name"],
        "accounts": [
            {k: v for k, v in account.items() if k != "transactions"}
            for account in user["accounts"]
        ],
    }


@app.get("/openbanking/v1/transactions", tags=["open-banking"])
def transactions(
    account_id: str = Query(..., description="Account to read"),
    authorization: str | None = Header(None),
) -> dict:
    """Transactions for one account.

    The account must belong to the consenting user. This is the per-user
    isolation beat: a token minted for Julian cannot read Elena's account even
    though the agent, the tool and the endpoint are identical.
    """
    claims = _require_bearer(authorization, need_scope="openbanking.transactions.read", subject_required=True)
    user = data.BANK_USERS.get(claims["sub"])
    if not user:
        raise HTTPException(status_code=404, detail="No banking profile for the consenting user.")

    for account in user["accounts"]:
        if account["account_id"] == account_id:
            return {
                "user": user["display_name"],
                "account_id": account_id,
                "transactions": account["transactions"],
            }

    raise HTTPException(
        status_code=403,
        detail=(
            f"Account {account_id} does not belong to {claims['sub']}. "
            "The consent granted covers only that user's own accounts."
        ),
    )


@app.get("/openbanking/v1/income", tags=["open-banking"])
def income(authorization: str | None = Header(None)) -> dict:
    """Bank-verified income for the consenting user, to cross-check tax returns."""
    claims = _require_bearer(authorization, need_scope="openbanking.income.verify", subject_required=True)
    user = data.BANK_USERS.get(claims["sub"])
    if not user:
        raise HTTPException(status_code=404, detail="No banking profile for the consenting user.")

    return {
        "user": user["display_name"],
        "employer": user["employer"],
        "verified_annual_income_usd": user["verified_annual_income_usd"],
        "source": "Cymbal Open Banking - payroll deposit analysis",
    }


# -- OAuth2 ---------------------------------------------------------------

@app.get("/oauth/authorize", response_class=HTMLResponse, tags=["oauth"])
def authorize_form(
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    scope: str = Query(""),
    state: str = Query(""),
    response_type: str = Query("code"),
    code_challenge: str = Query(""),
    code_challenge_method: str = Query("S256"),
) -> HTMLResponse:
    """Consent page.

    The mock bank has no real login, so the user picks which demo persona they
    are signing in as. That selector is what makes per-user isolation
    demonstrable: consent as Julian, and the vaulted token reaches only
    Julian's data.
    """
    if client_id != auth.THREE_LO_CLIENT_ID:
        raise HTTPException(status_code=400, detail="Unknown client_id.")
    if response_type != "code":
        raise HTTPException(status_code=400, detail="Only response_type=code is supported.")
    if not auth.redirect_uri_allowed(redirect_uri):
        raise HTTPException(
            status_code=400,
            detail=f"redirect_uri not allow-listed: {redirect_uri}",
        )

    requested = [s for s in scope.split() if s]
    unknown = [s for s in requested if s not in data.SUPPORTED_SCOPES]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unsupported scope(s): {', '.join(unknown)}")

    scope_rows = "".join(
        f"<li><code>{html.escape(s)}</code><br><span>{html.escape(data.SUPPORTED_SCOPES[s])}</span></li>"
        for s in requested
    )
    people = "".join(
        f'<option value="{html.escape(sub)}">{html.escape(u["display_name"])} ({html.escape(sub)})</option>'
        for sub, u in data.BANK_USERS.items()
    )
    hidden = "".join(
        f'<input type=hidden name="{k}" value="{html.escape(v)}">'
        for k, v in {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
        }.items()
    )

    return HTMLResponse(f"""<!doctype html><meta charset=utf-8>
<title>Cymbal Bank - authorize access</title>
<style>
 body{{font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
      max-width:30rem;margin:3rem auto;padding:2rem;color:#1f2328;
      border:1px solid #d8dee4;border-radius:12px}}
 h1{{font-size:1.2rem;margin:0 0 .3rem}} .sub{{color:#59636e;font-size:.9rem;margin:0 0 1.3rem}}
 ul{{list-style:none;padding:0;margin:0 0 1.3rem}}
 li{{border:1px solid #e8ecf0;border-radius:8px;padding:.6rem .8rem;margin-bottom:.5rem}}
 li span{{color:#59636e;font-size:.86rem}}
 code{{background:#f2f4f7;padding:.1em .35em;border-radius:4px;font-size:.85em}}
 select,button{{width:100%;padding:.6rem;border-radius:8px;border:1px solid #d8dee4;font-size:1rem}}
 button{{margin-top:.8rem;background:#1a56db;color:#fff;border:none;font-weight:600;cursor:pointer}}
 .deny{{background:none;color:#59636e;border:1px solid #d8dee4;font-weight:400;margin-top:.4rem}}
 .note{{margin-top:1.3rem;font-size:.8rem;color:#848d97}}
</style>
<h1>Cymbal Bank</h1>
<p class=sub><strong>Partner Access Agent</strong> is requesting access to your account.</p>
<ul>{scope_rows}</ul>
<form method=post>
  {hidden}
  <label class=sub for=sub>Signing in as (demo persona)</label>
  <select id=sub name=sub>{people}</select>
  <button name=decision value=allow type=submit>Allow</button>
  <button name=decision value=deny type=submit class=deny>Deny</button>
</form>
<p class=note>Synthetic consent screen for the GEAP Auth Manager demo. No real
bank, no real accounts.</p>
""")


@app.post("/oauth/authorize", tags=["oauth"])
def authorize_submit(
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    scope: str = Form(""),
    state: str = Form(""),
    sub: str = Form(...),
    decision: str = Form("deny"),
    code_challenge: str = Form(""),
    code_challenge_method: str = Form("S256"),
) -> RedirectResponse:
    """Record the decision and bounce back to the caller with a code."""
    if not auth.redirect_uri_allowed(redirect_uri):
        raise HTTPException(status_code=400, detail="redirect_uri not allow-listed.")

    params: dict[str, str] = {}
    if decision != "allow":
        params["error"] = "access_denied"
    elif sub not in data.BANK_USERS:
        params["error"] = "access_denied"
        params["error_description"] = "unknown user"
    else:
        params["code"] = auth.mint(
            {
                "sub": sub,
                "scope": scope,
                "client_id": client_id,
                "typ": "code",
                "cc": code_challenge,
                "ccm": code_challenge_method,
                "redirect_uri": redirect_uri,
            },
            auth.AUTH_CODE_TTL,
        )
    if state:
        params["state"] = state

    return RedirectResponse(f"{redirect_uri}?{urllib.parse.urlencode(params)}", status_code=302)


@app.post("/oauth/token", tags=["oauth"])
async def token(request: Request) -> JSONResponse:
    """Token endpoint: client_credentials (2LO) and authorization_code (3LO).

    Client credentials are accepted in the body or via HTTP Basic, since
    different clients send them differently.
    """
    form = await request.form()
    grant_type = form.get("grant_type", "")

    client_id = form.get("client_id", "")
    client_secret = form.get("client_secret", "")
    basic = request.headers.get("authorization", "")
    if not client_id and basic.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(basic.split(" ", 1)[1]).decode()
            client_id, _, client_secret = decoded.partition(":")
        except Exception:
            pass

    if grant_type == "client_credentials":
        if client_id != auth.TWO_LO_CLIENT_ID or client_secret != auth.TWO_LO_CLIENT_SECRET:
            return JSONResponse({"error": "invalid_client"}, status_code=401)

        # No `sub`: this token represents the agent itself, not a user.
        granted = form.get("scope") or "insurance.quote"
        return JSONResponse({
            "access_token": auth.mint({"scope": granted, "client_id": client_id}, auth.ACCESS_TOKEN_TTL),
            "token_type": "Bearer",
            "expires_in": auth.ACCESS_TOKEN_TTL,
            "scope": granted,
        })

    if grant_type == "authorization_code":
        if client_id != auth.THREE_LO_CLIENT_ID or client_secret != auth.THREE_LO_CLIENT_SECRET:
            return JSONResponse({"error": "invalid_client"}, status_code=401)

        claims = auth.verify(form.get("code", ""))
        if not claims or claims.get("typ") != "code":
            return JSONResponse({"error": "invalid_grant"}, status_code=400)

        if not auth.verify_pkce(claims.get("cc", ""), claims.get("ccm", "S256"), form.get("code_verifier", "")):
            return JSONResponse({"error": "invalid_grant", "error_description": "PKCE check failed"}, status_code=400)

        granted = claims.get("scope", "")
        return JSONResponse({
            "access_token": auth.mint({"sub": claims["sub"], "scope": granted, "client_id": client_id}, auth.ACCESS_TOKEN_TTL),
            "refresh_token": auth.mint({"sub": claims["sub"], "scope": granted, "typ": "refresh"}, auth.ACCESS_TOKEN_TTL * 24),
            "token_type": "Bearer",
            "expires_in": auth.ACCESS_TOKEN_TTL,
            "scope": granted,
        })

    if grant_type == "refresh_token":
        claims = auth.verify(form.get("refresh_token", ""))
        if not claims or claims.get("typ") != "refresh":
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return JSONResponse({
            "access_token": auth.mint({"sub": claims["sub"], "scope": claims.get("scope", "")}, auth.ACCESS_TOKEN_TTL),
            "token_type": "Bearer",
            "expires_in": auth.ACCESS_TOKEN_TTL,
            "scope": claims.get("scope", ""),
        })

    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
