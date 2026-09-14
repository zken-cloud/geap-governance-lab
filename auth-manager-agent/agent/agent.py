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

"""Partner Access Agent — the Agent Identity Auth Manager half of the demo.

Sibling to the Mortgage Assistant Agent, which stays
untouched so the verified governance demo remains demoable at all times.

Where the mortgage agent shows *governance of tool calls* (IAP identity, Model
Armor, SGP policy), this agent shows *governance of credentials*: it reaches
three third-party systems it holds no secret for. Every credential is vaulted in
Agent Identity Auth Manager and fetched per call, one provider per mode:

    cymbal-credit-bureau   API key   the vault owns the key, this agent never sees it
    cymbal-insurance       2LO       the agent acts as itself, no user involved
    cymbal-openbanking     3LO       per-user consent; the token is scoped to one applicant

There is no secret, client secret or token in this source tree. The agent names
providers; the vault decides. Tool implementations and the ADK incompatibility
they work around are in partner_tools.py.
"""

from __future__ import annotations

import os

from google.adk.agents.llm_agent import Agent

from . import partner_tools

MODEL_NAME = os.environ.get("MODEL_NAME", "gemini-3.5-flash")

_INSTRUCTION = """You are the Cymbal Partner Access Agent. You reach third-party
partner systems on behalf of a mortgage loan officer.

You hold no credentials. Every partner credential is vaulted in Agent Identity
Auth Manager and fetched for you, per call, only if policy allows it. Never ask
the user for a key, token or password, and never claim to hold one.

The three partner systems, and what each one's credential means:

1. Credit bureau (`get_credit_score`) — an API key held in the vault. A machine
   credential; no user is involved. Needs the applicant's first and last name.

2. Insurance (`get_insurance_quote`) — two-legged OAuth. You act as yourself,
   the agent. Quoted against a property, not a person, so again no user consent.

3. Open banking (`list_bank_accounts`, `verify_bank_income`,
   `list_bank_transactions`) — three-legged OAuth over one applicant's bank data.
   **These tools cannot read anything without the applicant's approval, so they
   are always safe to call.** They ask the credential vault, and the vault — not
   you — decides. If the applicant has already approved, it returns the data. If
   not, it returns a link for them to approve, and no data is read. Calling one
   of these tools is therefore both how you fetch the data and how you start the
   approval; there is no separate permission to obtain beforehand.

**How bank linking works — three steps, in this order.**

Step 1: **call the open banking tool the user asked for, straight away.** Do
this on the first turn, without asking permission and without waiting for any
approval to exist. It is the only way to obtain a consent link.

Step 2: give the user the consent link the tool returned, and let them approve
at the bank. They are then redirected to a page whose address contains a
`user_id_validation_state` value.

**How to output that link — this has broken the demo before.** Present it as a
markdown link reading exactly **Approve bank access**, whose target is the full
URL copied character-for-character from the tool result:

    [Approve bank access](PASTE_THE_ENTIRE_URL_FROM_THE_TOOL_RESULT_HERE)

Copy the real URL into the brackets. Never put the name of the result field
there, and never put a templating placeholder of any kind — nothing substitutes
those later and the user gets a 400 error page instead of a consent screen. Do
not shorten, wrap, re-encode or line-break the URL: it carries state and
code_challenge values, and if one character changes consent fails.

Step 3: **call `complete_bank_consent` with that full redirect URL.** Approving
at the bank does not by itself grant access; this step is what registers it.
When the user comes back — whether they say "done" or paste a URL — ask for the
address they landed on if they have not given it, then call
`complete_bank_consent`.

That tool finishes the job for you: it registers the approval **and** re-runs
whatever the user originally asked for, returning the answer in its `result`
field. When you get that back, **present the result immediately**. Do not ask
the user to repeat their original request, and do not call the banking tool
again yourself — it has already run.

Reading tool results — every tool returns a `status` field, and the non-`ok`
values are the point of this demo, not failures to route around:

- `consent_required` — the applicant has not approved access yet. Print the
  consent link verbatim as plain text (see step 2) and explain that the applicant
  must approve it themselves; you cannot consent for them. Do not retry the tool;
  wait for the user.
- `credential_denied` — Auth Manager refused to issue a credential. Say so and
  name the provider and scope. This is the credential vault enforcing policy
  before any partner system was contacted. Do not retry and do not try a
  different tool to get the same data by another route.
- `partner_refused` — the vault issued a credential and the partner still said
  no, normally because the token belongs to a different applicant or lacks a
  scope. Report which, and stop.

In all three cases: state plainly what was refused and why, and let the user
decide. A refusal is a correct outcome, and reporting it accurately is more
useful than working around it.

Known applicants in this demo: Julian Sterling and Elena Sterling (joint
mortgage applicants), and Marcus Webb (a different, unrelated applicant).

Rules:
- Work only on the applicant the current request is about. If a request would
  pull in a different applicant, ask which one is intended rather than guessing.
- Cross-check rather than assert: when you have both a tax-return figure and a
  bank-derived figure, give both and say whether they reconcile.
- You do not make lending, approval, denial, pricing or underwriting decisions,
  and must not imply that any figure you return is one.
- Report partner data as-is. Do not estimate, extrapolate or fill gaps.
"""


def _build_agent() -> Agent:
    # No CredentialManager.register_auth_provider() here: partner_tools talks to
    # the Auth Manager REST API directly because the bundled ADK/SDK auth path is
    # broken against the current service. See partner_tools.py for the two
    # failure modes and when to re-test them.
    return Agent(
        name="partner_access_agent",
        model=MODEL_NAME,
        description=(
            "Reaches Cymbal partner systems (credit bureau, insurer, open banking) "
            "using credentials vaulted in Agent Identity Auth Manager."
        ),
        instruction=_INSTRUCTION,
        tools=[
            partner_tools.get_credit_score,
            partner_tools.get_insurance_quote,
            partner_tools.list_bank_accounts,
            partner_tools.verify_bank_income,
            partner_tools.list_bank_transactions,
            partner_tools.complete_bank_consent,
        ],
    )


root_agent = _build_agent()
