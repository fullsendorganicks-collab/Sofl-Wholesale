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

# absentee_owner alone is a weaker distress signal than tax_delinquent,
# pre_foreclosure, or lis_pendens_filed -- require higher equity to qualify
# on that signal alone. See the real-lead reasoning in ingest_leads() below.
WEAK_SIGNAL_MIN_EQUITY_PERCENT = 50.0

# ⚠️ COST CORRECTION, 2026-07-28: property/search is NOT free. Earlier
# comments/docs in this file incorrectly stated it was, based on a
# misreading of BatchData's pricing page. Confirmed via BatchData support
# and a real itemized consumption-report pull: search is billed PER RECORD
# RETURNED, roughly $0.096/property (a single call with take=20 cost
# $12.80 -- $0.64/property was also observed on a smaller call, so the
# per-record rate may vary; treat it as "not free, roughly $0.10/record,
# verify before assuming otherwise"). A "dry_run" that only skips
# skip_trace() is NOT actually free if it still calls search with a large
# `limit` -- this is exactly what caused a real, unintended ~$12.80 charge.
#
# Hard ceiling: no search call in this module may request more than this
# many records, regardless of what limit/take value is passed in from
# main.py or anywhere else. This cannot be overridden by a caller mistake.
MAX_SEARCH_RECORDS_PER_CALL = 10


def _require_api_key() -> None:
    if not BATCHDATA_API_KEY:
        raise RuntimeError(
            "BATCHDATA_API_KEY is not set. Sign up at batchdata.com and add the key "
            "to your environment before pulling leads -- until then, leads can be "
            "added to the `leads` table manually."
        )


# CONFIRMED via BatchData support, 2026-07-29: the real, supported way to
# filter /property/search server-side is a `quickLists` array of string
# tags inside searchCriteria, combined with AND logic -- NOT flat top-level
# booleans like {"absenteeOwner": true}, which support confirmed is simply
# not a supported filter and silently does nothing (explains why the
# earlier version of this code saw absenteeOwner:true requested but got
# back properties with quickLists.absenteeOwner=false in the response).
#
# Valid quickLists filter tags (per support, may not be exhaustive):
#   "absentee-owner", "tax-default", "preforeclosure", "notice-of-default",
#   "high-equity" (>20% equity -- there is NO custom equity range filter,
#   e.g. no way to request "30%+" specifically; high-equity is the only
#   equity-related tag and it's a fixed >20% threshold)
QUICKLIST_TAG_MAP = {
    "absentee_owner": "absentee-owner",
    "tax_delinquent": "tax-default",
    "pre_foreclosure": "preforeclosure",
    "notice_of_default": "notice-of-default",
    "high_equity": "high-equity",
}


def count_matching_properties(county: str, state: str, zip_codes: list[str] | None = None,
                               quicklists: list[str] | None = None) -> dict:
    """
    CONFIRMED via BatchData support, 2026-07-29: sending "take": 0 returns
    aggregate count/summary data only, and is billed as ONE property record
    at the dataset rate (not per-matching-record) -- this is the real,
    near-minimum-cost way to see how many properties match a filter BEFORE
    committing to a real search call. Use this before every real
    ingest_leads() call on a new county/quicklists combination, not the
    misleadingly-named old "dry_run" (which still pulls up to 10 real,
    billed records -- see ingest_leads()'s dry_run docs).

    Also confirmed: price per record does NOT vary by which quickLists
    filter is used -- billing is by dataset/token provisioning, not by
    which filter tags are applied. Filtering narrows what's returned (so
    you're not paying for irrelevant records), it does not change the
    per-record rate itself.

    ⚠️ UNVERIFIED: the exact field name BatchData uses for the count in a
    take=0 response has NOT been confirmed against a real call yet (this
    function was written from the support chat's description, not an
    observed response). Tries a few plausible field names
    (results.meta.total, results.totalCount, results.total); if NONE of
    them match, returns count=None and includes the full raw response so
    the real field name is visible instead of silently returning a wrong
    0. Check the raw_response the first time this runs for real.
    """
    _require_api_key()
    tags = [QUICKLIST_TAG_MAP[q] for q in (quicklists or []) if q in QUICKLIST_TAG_MAP]
    search_criteria = {
        "query": f"{county} County, {state}",
        "zipCodes": zip_codes or [],
    }
    if tags:
        search_criteria["quickLists"] = tags
    payload = {"searchCriteria": search_criteria, "options": {"take": 0}}
    headers = {"Authorization": f"Bearer {BATCHDATA_API_KEY}", "Content-Type": "application/json"}

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(f"{BATCHDATA_BASE_URL}/property/search", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", {}) or {}
    count = None
    for path in [("meta", "total"), ("totalCount",), ("total",)]:
        node = results
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, int):
            count = node
            break

    if count is None:
        print(f"[batchdata_service] count_matching_properties: could not find count field, "
              f"raw response: {data}")

    return {"count": count, "raw_response": data}


def search_distressed_properties(county: str, state: str, zip_codes: list[str] | None = None,
                                  quicklists: list[str] | None = None, limit: int = 10) -> list[dict]:
    """
    Pulls a list of properties for a county/state (optionally narrowed by
    zip). `quicklists` should be a list of our internal signal names (see
    QUICKLIST_TAG_MAP keys, e.g. ["absentee_owner", "tax_delinquent"]) --
    translated to BatchData's real quickLists tags before the request.
    This is the CONFIRMED correct server-side filter mechanism (see
    QUICKLIST_TAG_MAP comment above) -- using it actually narrows what
    gets returned and billed, unlike the old top-level-boolean approach
    which silently filtered nothing.

    Even with server-side filtering, ingest_leads() below still re-checks
    every returned property's real quickLists/valuation fields itself
    before inserting anything -- server-side filtering reduces cost, it
    does not replace the client-side qualifying logic.

    ⚠️ THIS IS A PAID CALL, billed per record returned (~$0.096-0.64/record
    observed for real, not free -- see MAX_SEARCH_RECORDS_PER_CALL comment
    above). Confirmed via BatchData support: price per record does NOT vary
    by which quickLists filter is applied -- it's a flat dataset rate.
    `limit` is hard-capped at MAX_SEARCH_RECORDS_PER_CALL (10) regardless
    of what's requested, so a caller mistake (or a default argument like
    the old `limit: int = 100`) can't cause a large, unexpected charge
    again. **Call count_matching_properties() first** to see how many
    results would match, for near-minimum cost, before calling this.
    """
    _require_api_key()
    limit = min(limit, MAX_SEARCH_RECORDS_PER_CALL)
    tags = [QUICKLIST_TAG_MAP[q] for q in (quicklists or []) if q in QUICKLIST_TAG_MAP]
    search_criteria = {
        "query": f"{county} County, {state}",
        "zipCodes": zip_codes or [],
    }
    if tags:
        search_criteria["quickLists"] = tags
    payload = {
        "searchCriteria": search_criteria,
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

    # Defensive against shape: a real call on 2026-07-28 hit `results[0]`
    # with a KeyError, meaning `results` was NOT a plain list as assumed
    # (likely a dict, e.g. {"persons": [...]} or similar -- BatchData's
    # skip-trace response shape wasn't confirmed before that call). Handle
    # both a list and a dict-with-a-list-inside without guessing further;
    # log the raw shape once so it's visible if this still doesn't match.
    results = data.get("results", [])
    if isinstance(results, list):
        return results[0] if results else {}
    if isinstance(results, dict):
        for key in ("persons", "matches", "properties", "records"):
            inner = results.get(key)
            if isinstance(inner, list) and inner:
                return inner[0]
        print(f"[batchdata_service] skip_trace: unrecognized results shape, keys={list(results.keys())}")
        return results
    print(f"[batchdata_service] skip_trace: unexpected results type {type(results)}: {data}")
    return {}


def ingest_leads(org_id: str, county: str, state: str, zip_codes: list[str] | None = None,
                  quicklists: list[str] | None = None, limit: int = 10,
                  min_equity_percent: float = 30.0, max_skip_traces: int = 5,
                  dry_run: bool = False) -> dict:
    """
    Pulls properties + skip traces each one + inserts into `leads` table.

    `quicklists`: which distress signals to filter for SERVER-SIDE (see
    QUICKLIST_TAG_MAP for valid values, e.g. ["absentee_owner",
    "tax_delinquent"]). Defaults to requiring at least one of
    tax_delinquent/pre_foreclosure/notice_of_default if not specified --
    NOT absentee_owner alone, since that's the weak signal that needs the
    higher equity bar anyway (see WEAK_SIGNAL_MIN_EQUITY_PERCENT below).
    This is the fix for the real cost problem from 2026-07-28: without a
    real server-side filter, most returned (and BILLED) records didn't
    qualify at all -- confirmed via BatchData support that the earlier
    top-level-boolean filter attempt was not a supported filter and did
    nothing, so every search call was paying for a lot of noise.

    HARD QUALIFYING FILTER (not just scoring): a property is only inserted
    if it has AT LEAST ONE real distress signal (tax delinquent, absentee
    owner, pre-foreclosure, lis pendens) AND meets the minimum equity
    threshold. A normal MLS-listed, owner-occupied home with no distress
    signal gets REJECTED here, not just scored low later -- this enforces
    "only properties that actually work for wholesaling enter the pipeline
    at all," rather than trusting lead_scoring.py to sort it out downstream.
    This client-side check still runs even with server-side filtering,
    since quickLists tags narrow the search but the qualifying logic here
    (including the strong/weak signal equity split) is more specific than
    what BatchData's filter alone guarantees.

    ⚠️ BOTH search AND skip_trace ARE PAID CALLS. An earlier version of this
    module incorrectly claimed property search was free ("Property Search
    Sessions: $0.00" on BatchData's pricing page, misread/mislabeled) --
    this caused a real, unintended ~$12.80 charge on a single "dry run"
    call on 2026-07-28 that only skipped skip_trace(), not search. Confirmed
    via BatchData support + a real itemized consumption-report pull: search
    is billed per record returned, roughly $0.10-0.64/record observed.
    `limit` is hard-capped at MAX_SEARCH_RECORDS_PER_CALL (10) inside
    search_distressed_properties() regardless of what's passed here.

    max_skip_traces caps how many properties get skip-traced (~$0.07/property,
    confirmed accurate) per call -- once hit, further qualifying properties
    are reported as "capped", not silently skip-traced anyway.

    dry_run=True skips skip_trace() and the database insert, but STILL
    calls search (which still costs money, capped at 10 records) -- this
    flag answers "what would qualify," it is NOT a zero-cost preview.
    There is currently no way to preview qualifying leads at truly zero
    cost; the closest is calling with a small `limit`.

    Returns a dict with counts: inserted vs rejected vs capped, plus
    skip_traces_used and its estimated cost -- search cost is NOT included
    in estimated_cost_usd because it varies per record and isn't reliably
    predictable; check BatchData's wallet/consumption-report endpoint for
    actual total spend, don't trust this function's estimate as complete.
    """
    if quicklists is None:
        # Default: only search for strong-signal properties server-side.
        # absentee_owner alone is deliberately excluded from the default --
        # it's the weak signal that needs a higher equity bar (see below),
        # so a broad absentee-owner search tends to return a lot of records
        # that get rejected anyway. Pass quicklists=["absentee_owner"]
        # explicitly if that's specifically what's wanted.
        quicklists = ["tax_delinquent", "pre_foreclosure", "notice_of_default"]

    properties = search_distressed_properties(county, state, zip_codes, quicklists, limit)
    inserted, rejected, capped, rejected_weak_signal = 0, 0, 0, 0
    skip_traces_used = 0

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

                # HARD REJECT: no distress signal at all.
                if not distress_signals:
                    rejected += 1
                    continue

                # STRONG vs WEAK signal distinction (added 2026-07-28, per
                # real lead review): a real BatchData lead scored 32/100 by
                # Claude on absentee_owner alone -- "high property value and
                # lack of stronger distress signals... likely a passive
                # investor with little urgency to accept a steep discount."
                # tax_delinquent, pre_foreclosure, and lis_pendens are strong
                # signals of real seller distress/urgency and qualify at the
                # normal min_equity_percent threshold. absentee_owner alone
                # is weaker -- an absentee landlord isn't necessarily
                # motivated to sell below market -- so if that's the ONLY
                # signal present, require WEAK_SIGNAL_MIN_EQUITY_PERCENT
                # (50%) instead, not just min_equity_percent (default 30%).
                strong_signals = {"tax_delinquent", "pre_foreclosure", "lis_pendens_filed"}
                has_strong_signal = any(s in strong_signals for s in distress_signals)
                required_equity = min_equity_percent if has_strong_signal else WEAK_SIGNAL_MIN_EQUITY_PERCENT

                if equity_percent < required_equity:
                    if has_strong_signal:
                        rejected += 1
                    else:
                        rejected_weak_signal += 1
                    continue

                # COST CAP: this property qualified, but skip-tracing it is a
                # real, paid call. If we've already hit max_skip_traces for
                # this run, stop spending -- report it as capped rather than
                # silently skip-tracing anyway. This is the fix for the real
                # incident on 2026-07-28 where uncapped skip-tracing across
                # several ingest calls spent far more than expected.
                if skip_traces_used >= max_skip_traces:
                    capped += 1
                    continue

                # VERIFIED 2026-07-28: /property/search's own response already
                # includes real owner name + mailing address under prop["owner"]
                # (owner.fullName, owner.names, owner.mailingAddress) -- confirmed
                # correct on a real lead ("Eva Maria Hernandez-Perucha"). Use that
                # directly rather than relying on skip_trace() for name/address;
                # skip_trace's actual job is phone/email, which search does NOT
                # return. Its response shape for those fields is still unconfirmed
                # (the one real call so far returned something that didn't have
                # name/phone/email at the top level either -- likely a full
                # property record via the "properties" fallback key, not a
                # person-contact object), so treat phone/email as best-effort.
                search_owner = prop.get("owner") or {}
                owner_name = search_owner.get("fullName")

                if dry_run:
                    # Would qualify and would be skip-traced, but dry_run means
                    # don't actually spend anything or write anything.
                    inserted += 1
                    continue

                trace = skip_trace(address, city, state, zip_code)
                skip_traces_used += 1
                trace_owner = trace.get("owner") or {}
                # Try a few plausible field-name variants for phone/email since
                # skip-trace's real shape for contact info is still unconfirmed.
                owner_phone = (trace_owner.get("phone") or trace.get("phone") or
                               trace.get("phoneNumber"))
                owner_email = (trace_owner.get("email") or trace.get("email"))

                # owner_mailing_address is a plain TEXT column, but the mailing
                # address field may come back as a nested object, not a string --
                # flatten it defensively rather than assume the shape, same
                # lesson as the search endpoint.
                mailing_address = search_owner.get("mailingAddress") or trace_owner.get("mailingAddress")
                if isinstance(mailing_address, dict):
                    mailing_address = ", ".join(
                        str(v) for v in
                        [mailing_address.get("street"), mailing_address.get("city"),
                         mailing_address.get("state"), mailing_address.get("zip")]
                        if v
                    )

                cur.execute("""
                    INSERT INTO leads (org_id, source, address, city, state, zip, county,
                                        owner_name, owner_phone, owner_email,
                                        owner_mailing_address, estimated_value,
                                        equity_estimate, distress_signals,
                                        is_lis_pendens_filed, raw_payload, status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'new')
                """, (
                    org_id, "batchdata", address, city, state, zip_code, county,
                    owner_name, owner_phone, owner_email,
                    mailing_address, estimated_value, estimated_equity,
                    __import__("json").dumps(distress_signals),
                    is_lis_pendens,
                    __import__("json").dumps(prop),
                ))
                inserted += 1

    return {
        "inserted": inserted,
        "rejected_no_distress_or_low_equity": rejected,
        "rejected_weak_signal_insufficient_equity": rejected_weak_signal,
        "capped_at_max_skip_traces": capped,
        "skip_traces_used": skip_traces_used,
        "estimated_cost_usd": round(skip_traces_used * 0.07, 2),
        "dry_run": dry_run,
    }
