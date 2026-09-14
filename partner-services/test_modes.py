"""End-to-end checks for the three Auth Manager credential modes.

Runs against the app in-process via httpx/ASGI - no server, no network. Every
assertion here corresponds to a beat in the demo script.
"""

import base64
import hashlib
import sys
import urllib.parse

sys.path.insert(0, "app")

from fastapi.testclient import TestClient
from main import app

C = TestClient(app, follow_redirects=False)
FAILURES = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f"  -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


print("\n=== API-key mode: credit bureau ===")
r = C.get("/credit-bureau/v1/score", params={"first_name": "Julian", "last_name": "Sterling"},
          headers={"X-API-Key": "cymbal-credit-key-alpha"})
check("X-API-Key (what the vault injects) returns the credit file", r.status_code == 200 and r.json()["score"] == 742, r.text[:120])

r = C.get("/credit-bureau/v1/score", params={"first_name": "Julian", "last_name": "Sterling"},
          headers={"X-GOOG-API-KEY": "cymbal-credit-key-alpha"})
check("X-GOOG-API-KEY still accepted as an alias", r.status_code == 200, r.text[:120])

r = C.get("/credit-bureau/v1/score", params={"first_name": "Julian", "last_name": "Sterling"})
check("missing key -> 401", r.status_code == 401)

r = C.get("/credit-bureau/v1/score", params={"first_name": "Julian", "last_name": "Sterling"},
          headers={"X-API-Key": "rotated-away"})
check("stale key -> 401 (the rotation demo)", r.status_code == 401)


print("\n=== 2LO: insurance, client credentials ===")
r = C.post("/oauth/token", data={"grant_type": "client_credentials",
                                 "client_id": "cymbal-insurance-client",
                                 "client_secret": "cymbal-insurance-secret",
                                 "scope": "insurance.quote"})
check("token issued", r.status_code == 200 and "access_token" in r.json(), r.text[:160])
two_lo = r.json().get("access_token", "")

r = C.post("/oauth/token", data={"grant_type": "client_credentials",
                                 "client_id": "cymbal-insurance-client",
                                 "client_secret": "wrong"})
check("bad secret -> 401", r.status_code == 401)

basic = base64.b64encode(b"cymbal-insurance-client:cymbal-insurance-secret").decode()
r = C.post("/oauth/token", data={"grant_type": "client_credentials"},
           headers={"Authorization": f"Basic {basic}"})
check("HTTP Basic client auth also accepted", r.status_code == 200)

r = C.get("/insurance/v1/quote", params={"property_value_usd": 520000, "address": "1428 Juniper Lane, Cedar Park, TX"},
          headers={"Authorization": f"Bearer {two_lo}"})
check("quote returned", r.status_code == 200 and r.json()["annual_premium_usd"] > 0, r.text[:160])
check("new-roof discount applied", r.status_code == 200 and r.json()["discounts_applied"], r.text[:120])

r = C.get("/insurance/v1/quote", params={"property_value_usd": 520000})
check("no token -> 401", r.status_code == 401)

r = C.get("/openbanking/v1/accounts", headers={"Authorization": f"Bearer {two_lo}"})
check("2LO token rejected on a 3LO endpoint", r.status_code == 403, r.text[:160])


print("\n=== 3LO: open banking, user consent ===")
REDIRECT = "https://agentidentitycredentials.googleapis.com/v1alpha/projects/p/locations/us-central1/authProviders/n/oauthcallback"
SCOPES = "openbanking.accounts.read openbanking.transactions.read openbanking.income.verify"
verifier = "demo-code-verifier-0123456789"
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def consent(sub):
    """Drive the consent screen for one persona and return their access token."""
    r = C.get("/oauth/authorize", params={"client_id": "cymbal-openbanking-client",
                                          "redirect_uri": REDIRECT, "scope": SCOPES,
                                          "state": "xyz", "response_type": "code",
                                          "code_challenge": challenge, "code_challenge_method": "S256"})
    assert r.status_code == 200, r.text[:200]
    r = C.post("/oauth/authorize", data={"client_id": "cymbal-openbanking-client",
                                         "redirect_uri": REDIRECT, "scope": SCOPES, "state": "xyz",
                                         "sub": sub, "decision": "allow",
                                         "code_challenge": challenge, "code_challenge_method": "S256"})
    assert r.status_code == 302, r.text[:200]
    code = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["location"]).query)["code"][0]
    r = C.post("/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                     "client_id": "cymbal-openbanking-client",
                                     "client_secret": "cymbal-openbanking-secret",
                                     "code_verifier": verifier})
    assert r.status_code == 200, r.text[:200]
    return r.json()["access_token"]


r = C.get("/oauth/authorize", params={"client_id": "cymbal-openbanking-client",
                                      "redirect_uri": REDIRECT, "scope": SCOPES, "response_type": "code"})
check("consent page renders with scope descriptions", r.status_code == 200 and "View your transaction history" in r.text)
check("consent page offers both personas", "julian.sterling@example.com" in r.text and "elena.sterling@example.com" in r.text)

r = C.get("/oauth/authorize", params={"client_id": "cymbal-openbanking-client",
                                      "redirect_uri": "https://evil.example/cb", "scope": SCOPES, "response_type": "code"})
check("un-allow-listed redirect_uri rejected", r.status_code == 400)

r = C.get("/oauth/authorize", params={"client_id": "cymbal-openbanking-client", "redirect_uri": REDIRECT,
                                      "scope": "openbanking.everything", "response_type": "code"})
check("unknown scope rejected", r.status_code == 400)

r = C.post("/oauth/authorize", data={"client_id": "cymbal-openbanking-client", "redirect_uri": REDIRECT,
                                     "scope": SCOPES, "sub": "julian.sterling@example.com", "decision": "deny"})
check("deny -> access_denied, no code", r.status_code == 302 and "error=access_denied" in r.headers["location"])

julian = consent("julian.sterling@example.com")
elena = consent("elena.sterling@example.com")
check("Julian consented and got a token", bool(julian))
check("Elena consented and got a token", bool(elena))

r = C.get("/openbanking/v1/accounts", headers={"Authorization": f"Bearer {julian}"})
check("Julian sees his own account", r.status_code == 200 and r.json()["accounts"][0]["account_id"] == "acct-jl-7741", r.text[:160])

r = C.get("/openbanking/v1/transactions", params={"account_id": "acct-jl-7741"},
          headers={"Authorization": f"Bearer {julian}"})
check("Julian reads his own transactions", r.status_code == 200 and len(r.json()["transactions"]) == 6, r.text[:160])

r = C.get("/openbanking/v1/transactions", params={"account_id": "acct-el-3162"},
          headers={"Authorization": f"Bearer {julian}"})
check("*** per-user isolation: Julian CANNOT read Elena's account", r.status_code == 403, r.text[:200])

r = C.get("/openbanking/v1/transactions", params={"account_id": "acct-el-3162"},
          headers={"Authorization": f"Bearer {elena}"})
check("Elena reads her own account with the same tool", r.status_code == 200, r.text[:160])

r = C.get("/openbanking/v1/income", headers={"Authorization": f"Bearer {julian}"})
check("income cross-check matches the 2024 return", r.status_code == 200 and r.json()["verified_annual_income_usd"] == 150000, r.text[:160])

r = C.get("/oauth/authorize", params={"client_id": "cymbal-openbanking-client", "redirect_uri": REDIRECT,
                                      "scope": "openbanking.accounts.read", "response_type": "code"})
r2 = C.post("/oauth/authorize", data={"client_id": "cymbal-openbanking-client", "redirect_uri": REDIRECT,
                                      "scope": "openbanking.accounts.read",
                                      "sub": "julian.sterling@example.com", "decision": "allow"})
code = urllib.parse.parse_qs(urllib.parse.urlparse(r2.headers["location"]).query)["code"][0]
tok = C.post("/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                   "client_id": "cymbal-openbanking-client",
                                   "client_secret": "cymbal-openbanking-secret"}).json()["access_token"]
r = C.get("/openbanking/v1/transactions", params={"account_id": "acct-jl-7741"},
          headers={"Authorization": f"Bearer {tok}"})
check("*** scope governance: accounts-only token cannot read transactions", r.status_code == 403, r.text[:200])

r = C.post("/oauth/token", data={"grant_type": "authorization_code", "code": "not-a-code",
                                 "client_id": "cymbal-openbanking-client",
                                 "client_secret": "cymbal-openbanking-secret"})
check("forged code rejected", r.status_code == 400)

r2 = C.post("/oauth/authorize", data={"client_id": "cymbal-openbanking-client", "redirect_uri": REDIRECT,
                                      "scope": SCOPES, "sub": "julian.sterling@example.com", "decision": "allow",
                                      "code_challenge": challenge, "code_challenge_method": "S256"})
code = urllib.parse.parse_qs(urllib.parse.urlparse(r2.headers["location"]).query)["code"][0]
r = C.post("/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                 "client_id": "cymbal-openbanking-client",
                                 "client_secret": "cymbal-openbanking-secret",
                                 "code_verifier": "wrong-verifier"})
check("PKCE mismatch rejected", r.status_code == 400 and "PKCE" in r.text, r.text[:160])


print("\n=== misc ===")
check("healthz", C.get("/healthz").status_code == 200)
check("landing page", C.get("/").status_code == 200)
check("openapi docs", C.get("/openapi.json").status_code == 200)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILED: {FAILURES}")
    sys.exit(1)
print("All checks passed.")
