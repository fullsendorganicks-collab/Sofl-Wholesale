"""
Drafts an offer letter using Claude. IMPORTANT: this only creates a DRAFT.
Nothing gets sent until you (a human) approve it via the approve_offer()
function or the /offers/{id}/approve API endpoint. See README.md for why
this gate exists (TCPA + the fact that you are the legally binding party).
"""

import os
from anthropic import Anthropic
from app.db import get_conn

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

DRAFT_PROMPT = """Draft a short, direct, non-pushy letter offering to buy this person's
property in cash, as-is, with a fast closing. Write in a plain, respectful, human
voice -- not salesy, not overly formal, no hype language, no pressure tactics.

Property address: {address}
Offer amount: ${offer_amount:,.0f}
Sender name: {sender_name}
Sender phone: {sender_phone}

The letter must:
- Clearly state this is an offer on the sender's CONTRACTUAL INTEREST in the property,
  not a brokered sale (this is a Florida wholesaling compliance requirement -- do not
  describe this as "listing" or "selling" the property in a broker capacity)
- Mention the offer is contingent on inspection and normal contract terms
- Include a phone number and invite a call, no pressure to respond immediately
- Be under 200 words

Output ONLY the letter text, no preamble, no markdown formatting.
"""


def draft_offer(lead_id: str, offer_amount: float, sender_name: str, sender_phone: str) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM leads WHERE id = %s", (lead_id,))
            lead = cur.fetchone()
            if not lead:
                raise ValueError(f"Lead {lead_id} not found")
            cur.execute("SELECT * FROM orgs WHERE id = %s", (lead["org_id"],))
            org = cur.fetchone()

    # HARD GATE 1: general wholesaling / principal-buyer-exemption clearance.
    # An AI researching Florida Statute 475.011 is not the same as an
    # attorney confirming YOU specifically qualify. This blocks ALL offer
    # drafting, no exceptions, until your org record has been manually
    # flipped to TRUE after that real conversation happens.
    if not org.get("attorney_cleared_general_wholesaling"):
        raise PermissionError(
            "attorney_cleared_general_wholesaling is FALSE for your org. Before drafting "
            "any offer, get a Florida real estate attorney to confirm you qualify for the "
            "principal-buyer exemption under 475.011 -- this was researched, not confirmed "
            "by anyone with liability for being wrong. Update the orgs table once done."
        )

    # HARD GATE 2: foreclosure/lis-pendens clearance -- applies to ANY
    # lis-pendens lead, regardless of leaseback/repurchase structure. The
    # earlier leaseback-only reading of 501.1377 is a plausible
    # interpretation of the statute, not a confirmed legal conclusion --
    # some readings look at the totality of whether the deal is designed
    # around a homeowner's foreclosure distress, not just the leaseback
    # fact pattern specifically. Don't rely on this codebase's prior
    # narrower gate for that judgment call.
    if lead.get("is_lis_pendens_filed") and not org.get("attorney_cleared_foreclosure"):
        raise PermissionError(
            f"Lead {lead_id} ({lead['address']}) has an active foreclosure notice, and "
            "attorney_cleared_foreclosure is FALSE for your org. Florida Statute 501.1377 "
            "may apply beyond just leaseback/repurchase deals -- this needs an actual "
            "attorney confirming your specific letter/contract structure before you contact "
            "this seller, not an AI's reading of the statute. Update the orgs table once "
            "that conversation has happened."
        )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{"role": "user", "content": DRAFT_PROMPT.format(
            address=lead["address"], offer_amount=offer_amount,
            sender_name=sender_name, sender_phone=sender_phone,
        )}],
    )
    letter_text = response.content[0].text.strip()

    # SAFETY NET, not a guarantee: LLM output can drift from instructions.
    # This is a crude keyword check catching the most obvious broker-like
    # phrasing slips -- it does NOT replace you actually reading every
    # letter before approving it, but it flags an obvious red flag for you
    # to look at twice.
    risky_phrases = ["for sale", "listed at", "listing price", "now listing",
                      "MLS", "represent the seller", "represent you in this sale"]
    flagged_phrases = [p for p in risky_phrases if p.lower() in letter_text.lower()]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO offers (lead_id, offer_amount, draft_letter, approval_status)
                VALUES (%s, %s, %s, 'pending_review')
                RETURNING *
            """, (lead_id, offer_amount, letter_text))
            offer = cur.fetchone()

    if flagged_phrases:
        offer["compliance_warning"] = (
            f"AUTOMATED FLAG (not a guarantee -- read the full letter yourself): "
            f"drafted letter contains phrasing that may read as broker/listing language: "
            f"{', '.join(flagged_phrases)}. Review carefully before approving."
        )

    return offer


def approve_offer(offer_id: str, approved_by: str) -> dict:
    """Call this ONLY after you've personally read the draft letter and
    the offer amount and confirmed both are correct. This is the human
    checkpoint -- nothing sends without this being called first."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE offers
                SET approval_status = 'approved', approved_by = %s, approved_at = now()
                WHERE id = %s AND approval_status = 'pending_review'
                RETURNING *
            """, (approved_by, offer_id))
            offer = cur.fetchone()

    if not offer:
        raise ValueError("Offer not found or not in pending_review status")
    return offer


def reject_offer(offer_id: str) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE offers SET approval_status = 'rejected' WHERE id = %s RETURNING *
            """, (offer_id,))
            return cur.fetchone()
