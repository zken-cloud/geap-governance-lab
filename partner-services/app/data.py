"""Synthetic fixtures for Cymbal Partner Services.

Everything here is invented. The names deliberately match the Sterling family
already present in the legacy-dms fixtures (2023 and 2024 tax returns) so the
Auth Manager demo composes into the existing mortgage storyline rather than
sitting beside it.

No real people, accounts, or credit data.
"""

# -- Credit bureau (API-key mode) -----------------------------------------
# Keyed by lowercase "first last".

CREDIT_REPORTS = {
    "julian sterling": {
        "applicant": "Julian Sterling",
        "score": 742,
        "model": "CymbalScore 4.0",
        "band": "very good",
        "pulled_at": "2026-07-26",
        "factors": [
            "Length of credit history: 14 years",
            "Revolving utilisation: 18%",
            "No delinquencies in the last 7 years",
            "One hard inquiry in the last 12 months",
        ],
        "open_accounts": 6,
        "total_balance_usd": 24180,
    },
    "elena sterling": {
        "applicant": "Elena Sterling",
        "score": 768,
        "model": "CymbalScore 4.0",
        "band": "excellent",
        "pulled_at": "2026-07-26",
        "factors": [
            "Length of credit history: 16 years",
            "Revolving utilisation: 9%",
            "No delinquencies on record",
            "No hard inquiries in the last 12 months",
        ],
        "open_accounts": 4,
        "total_balance_usd": 9310,
    },
    # A third applicant belonging to nobody's current request -- the target for
    # the SGP "different applicant" deny demo.
    "marcus webb": {
        "applicant": "Marcus Webb",
        "score": 690,
        "model": "CymbalScore 4.0",
        "band": "good",
        "pulled_at": "2026-07-26",
        "factors": [
            "Revolving utilisation: 41%",
            "Two hard inquiries in the last 6 months",
        ],
        "open_accounts": 9,
        "total_balance_usd": 61450,
    },
}

# -- Insurance (2LO mode) -------------------------------------------------

PROPERTIES = {
    "1428 juniper lane": {
        "address": "1428 Juniper Lane, Cedar Park, TX 78613",
        "year_built": 2011,
        "square_feet": 2840,
        "construction": "brick veneer over wood frame",
        "roof_age_years": 4,
        "flood_zone": "X (minimal risk)",
    }
}

INSURANCE_PRODUCT = {
    "carrier": "Cymbal Mutual",
    "product": "Homeowners HO-3",
    "quote_valid_days": 30,
}


def quote_for(property_value_usd: int, address: str = "") -> dict:
    """Deterministic synthetic premium, so demo runs are reproducible."""
    prop = None
    for key, value in PROPERTIES.items():
        if key in address.strip().lower():
            prop = value
            break

    dwelling = int(property_value_usd * 0.85)
    base = dwelling * 0.0034
    roof_credit = 0.92 if prop and prop["roof_age_years"] <= 5 else 1.0
    annual = round(base * roof_credit, 2)

    return {
        **INSURANCE_PRODUCT,
        "property": prop or {"address": address or "unknown - indicative only"},
        "coverage": {
            "dwelling_usd": dwelling,
            "personal_property_usd": int(dwelling * 0.5),
            "liability_usd": 300000,
            "deductible_usd": 2500,
        },
        "annual_premium_usd": annual,
        "monthly_escrow_usd": round(annual / 12, 2),
        "discounts_applied": ["new roof (<= 5 years)"] if roof_credit < 1 else [],
    }


# -- Open banking (3LO mode) ----------------------------------------------
# Keyed by the end-user subject that consented. The point of the 3LO demo is
# that a token minted for one subject cannot read another subject's data.

BANK_USERS = {
    "julian.sterling@example.com": {
        "display_name": "Julian Sterling",
        "employer": "Northwind Logistics",
        "verified_annual_income_usd": 150000,
        "accounts": [
            {
                "account_id": "acct-jl-7741",
                "name": "Cymbal Everyday Checking",
                "type": "checking",
                "currency": "USD",
                "balance_usd": 12480.55,
                "transactions": [
                    {"date": "2026-07-15", "description": "NORTHWIND LOGISTICS PAYROLL", "amount_usd": 6250.00, "category": "income"},
                    {"date": "2026-07-08", "description": "BRIGHTLEAF GROCERY", "amount_usd": -412.83, "category": "groceries"},
                    {"date": "2026-07-03", "description": "CEDAR PARK MORTGAGE CO", "amount_usd": -2310.00, "category": "housing"},
                    {"date": "2026-07-01", "description": "NORTHWIND LOGISTICS PAYROLL", "amount_usd": 6250.00, "category": "income"},
                    {"date": "2026-06-15", "description": "NORTHWIND LOGISTICS PAYROLL", "amount_usd": 6250.00, "category": "income"},
                    {"date": "2026-06-01", "description": "NORTHWIND LOGISTICS PAYROLL", "amount_usd": 6250.00, "category": "income"},
                ],
            }
        ],
    },
    "elena.sterling@example.com": {
        "display_name": "Elena Sterling",
        "employer": "Meridian Health Group",
        "verified_annual_income_usd": 170000,
        "accounts": [
            {
                "account_id": "acct-el-3162",
                "name": "Cymbal Professional Checking",
                "type": "checking",
                "currency": "USD",
                "balance_usd": 28715.20,
                "transactions": [
                    {"date": "2026-07-20", "description": "MERIDIAN HEALTH GROUP", "amount_usd": 7083.33, "category": "income"},
                    {"date": "2026-07-06", "description": "CEDAR PARK UTILITIES", "amount_usd": -284.11, "category": "utilities"},
                    {"date": "2026-07-05", "description": "MERIDIAN HEALTH GROUP", "amount_usd": 7083.33, "category": "income"},
                    {"date": "2026-06-20", "description": "MERIDIAN HEALTH GROUP", "amount_usd": 7083.33, "category": "income"},
                    {"date": "2026-06-05", "description": "MERIDIAN HEALTH GROUP", "amount_usd": 7083.33, "category": "income"},
                ],
            }
        ],
    },
}

# Scopes the mock bank understands. The vault's allowedScopes should be a strict
# subset - requesting openbanking.payments.write is the scope-governance demo.
SUPPORTED_SCOPES = {
    "openbanking.accounts.read": "View your account names, types and balances",
    "openbanking.transactions.read": "View your transaction history",
    "openbanking.income.verify": "Confirm your verified income for a mortgage application",
    "openbanking.payments.write": "Initiate payments from your accounts",
}
