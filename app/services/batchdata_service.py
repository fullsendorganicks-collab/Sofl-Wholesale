"""
BatchData API integration.

Why BatchData instead of Zillow: Zillow has no public API in 2026 (shut down
in 2021). Their only official data path, Bridge Interactive, is restricted to
MLS members/licensed brokers ($500+/mo, weeks-long approval). Scraping Zillow
directly violates their Terms of Service and carries real legal exposure
(active anti-bot litigation, CFAA risk).

BatchData gives you legitimate, licensed access to property records, owner
contact info (skip tracing), and distress signals (tax delinquent, absentee
owner, pre-foreclosure, probate flags where available) through a real,
documented REST API. Sign up at batchdata.com to get BATCHDATA_API_KEY.

This module intentionally does ONE thing: pull raw property/owner data and
insert it into the `leads` table with status='new'. Scoring happens
separately in lead_scoring.py so you can re-run scoring without re-pulling data.

OPTIONAL: BatchData is not required for the rest of the app to run. If
BATCHDATA_API_KEY is unset, ingest_leads() raises a clear error when called
instead of crashing the whole app on import -- everything else (scoring,
offers, contracts, directives, daily brief) works fine without it. Add
leads manually until a BatchData key is set up.
"""

import os
import httpx
from app.db import get_conn

BATCHDATA_API_KEY = os.environ.get("BATCHDATA_API_KEY")
BATCHDATA_BASE_URL = "https://api.batchdata.com/api/v1"


def _require_api_key() -> None:
    if not BATCHDATA_API_KEY:
        raise RuntimeError(
            "BATCHDATA_API_KEY is not set. Sign up at batchdata.com and add the key "
            "to your environment before pulling leads -- until then, leads can be "
            "added to the `leads` table manually."
        )


def search_distressed_properties(county: str, state: str, zip_codes: list[str] | None = None,
                                  filters: dict | None = None, limit: int = 100) -> list[dict]:
    """
    Pulls a list of properties for a county/state (optionally narrowed by
    zip). The `filters` dict is passed through as extra searchCriteria
    keys, but treat it as a hint, not a guarantee: a live test on
    2026-07-28 sent {"absenteeOwner": True} and got back properties where
    quickLists.absenteeOwner was actually False for 2 of 3 results -- this
    request-side filter does NOT reliably narrow results as written
    (either the key/location is wrong per BatchData's real schema, or it's
    advisory-only on their end). Because of that, ingest_leads() below
    does NOT trust this filter -- it re-checks every returned property's
    real quickLists/valuation fields itself before inserting anything.
    Treat `filters` here as a way to *reduce* the response size / API cost
    for a rough pass, never as the actual qualifying logic.
    """
    _require_api_key()
    filters = filters or {}
    payload = {
        "searchCriteria": {
            "query": f"{county} County, {state}",
            "zipCodes": zip_codes or [],
            **filters,
        },
        "options": {"take": limit},
    }
    headers = {"Authorization": f"Bearer {BATCHDATA_API_KEY}", "Content-Type": "application/json"}

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(f"{BATCHDATA_BASE_URL}/property/search", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    return data.get("results", {}).get("properties", [])


def skip_trace(address: str, city: str, state: str, zip_code: str) -> dict:
    """
    Given a property address, returns owner contact info (name, phone, email,
    mailing address) if available. This is the paid "skip tracing" function --
    it's what turns "we know who owns this" into "we know how to reach them."
    """
    _require_api_key()
    payload = {"requests": [{"propertyAddress": {"street": address, "city": city,
                                                   "state": state, "zip": zip_code}}]}
    headers = {"Authorization": f"Bearer {BATCHDATA_API_KEY}", "Content-Type": "application/json"}

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(f"{BATCHDATA_BASE_URL}/property/skip-trace", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])
    return results[0] if results else {}


def ingest_leads(org_id: str, county: str, state: str, zip_codes: list[str] | None = None,
                  filters: dict | None = None, limit: int = 100,
                  min_equity_percent: float = 30.0) -> dict:
    """
    Pulls properties + skip traces each one + inserts into `leads` table.

    HARD QUALIFYING FILTER (not just scoring): a property is only inserted
    if it has AT LEAST ONE real distress signal (tax delinquent, absentee
    owner, pre-foreclosure, lis pendens) AND meets the minimum equity
    threshold. A normal MLS-listed, owner-occupied home with no distress
    signal gets REJECTED here, not just scored low later -- this enforces
    "only properties that actually work for wholesaling enter the pipeline
    at all," rather than trusting lead_scoring.py to sort it out downstream.

    Returns a dict with counts: inserted vs rejected, so the filter's
    actual effect is visible, not just trusted.
    """
    properties = search_distressed_properties(county, state, zip_codes, filters, limit)
    inserted, rejected = 0, 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            for prop in properties:
                address = prop.get("address", {}).get("street", "")
                city = prop.get("address", {}).get("city", "")
                zip_code = prop.get("address", {}).get("zip", "")

                # VERIFIED against a real BatchData /property/search response
                # on 2026-07-28 -- the actual shape nests everything under
                # sub-objects, not flat top-level fields like the first draft
                # of this code assumed:
                #   quickLists.{taxDefault,absenteeOwner,preforeclosure,...}
                #   foreclosure.status (key only present at all if applicable;
                #     "Notice of Lis Pendens" is one real observed value)
                #   valuation.{estimatedValue,equityCurrentEstimatedBalance,equityPercent}
                quick_lists = prop.get("quickLists") or {}
                distress_signals = []
                if quick_lists.get("taxDefault"):
                    distress_signals.append("tax_delinquent")
                if quick_lists.get("absenteeOwner"):
                    distress_signals.append("absentee_owner")
                if quick_lists.get("preforeclosure"):
                    distress_signals.append("pre_foreclosure")

                foreclosure_status = (prop.get("foreclosure") or {}).get("status", "")
                is_lis_pendens = "lis pendens" in foreclosure_status.lower()
                if is_lis_pendens:
                    distress_signals.append("lis_pendens_filed")

                valuation = prop.get("valuation") or {}
                estimated_value = valuation.get("estimatedValue") or 0
                estimated_equity = valuation.get("equityCurrentEstimatedBalance") or 0
                equity_percent = valuation.get("equityPercent")
                if equity_percent is None:
                    equity_percent = (estimated_equity / estimated_value * 100) if estimated_value else 0

                # HARD REJECT: no distress signal at all, OR equity too thin
                # to support a below-market cash offer. This is what keeps a
                # normal agent-listed, owner-occupied home OUT entirely.
                if not distress_signals or equity_percent < min_equity_percent:
                    rejected += 1
                    continue

                trace = skip_trace(address, city, state, zip_code)
                owner = trace.get("owner", {})

                cur.execute("""
                    INSERT INTO leads (org_id, source, address, city, state, zip, county,
                                        owner_name, owner_phone, owner_email,
                                        owner_mailing_address, estimated_value,
                                        equity_estimate, distress_signals,
                                        is_lis_pendens_filed, raw_payload, status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'new')
                """, (
                    org_id, "batchdata", address, city, state, zip_code, county,
                    owner.get("name"), owner.get("phone"), owner.get("email"),
                    owner.get("mailingAddress"), estimated_value, estimated_equity,
                    __import__("json").dumps(distress_signals),
                    is_lis_pendens,
                    __import__("json").dumps(prop),
                ))
                inserted += 1

    return {"inserted": inserted, "rejected_no_distress_or_low_equity": rejected}
