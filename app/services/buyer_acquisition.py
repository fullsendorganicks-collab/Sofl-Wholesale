"""
Buyer acquisition automation. This is the piece that actually determines
whether you make money -- a perfect lead pipeline is worthless if you have
no one to assign the contract to. Two functions:

1. prospect_cash_buyers() -- pulls recent cash-sale deed records from
   BatchData and inserts them into your `buyers` table as prospects.
   IMPORTANT CAVEAT: a cash sale 60-90 days ago proves this buyer HAD
   capital and closed a deal THEN -- it does not prove they're still
   actively buying now, still have capital deployed, or haven't moved
   markets/strategies since. Treat this as "worth contacting to find out,"
   not "confirmed active buyer." Expect a real conversion drop-off between
   prospected and actually responsive.
2. draft_buyer_intro() -- Claude drafts a short introduction email to a
   prospected buyer, describing your buy box and asking about theirs.

Compliance note (different from seller outreach): TCPA's strict marketing-
consent rules exist to protect consumers from being solicited. Emailing a
real estate investment company/LLC about a B2B buying relationship is a
different category, but CAN-SPAM still applies to any commercial email:
honest subject lines, a working unsubscribe/opt-out, and your real business
address. draft_buyer_intro() includes an unsubscribe line by default --
don't remove it.

This still goes through a light review step before sending -- not because
of the same legal weight as seller contact, but because a bad first
impression with a buyer you'll want a repeat relationship with is expensive
to undo.
"""

import os
import json
from anthropic import Anthropic
from app.db import get_conn

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

INTRO_PROMPT = """Draft a short (under 120 words) introduction email to a real estate
investor/cash buyer. This is a B2B outreach -- direct, professional, no hype.

Sender name: {sender_name}
Sender company: {sender_company}
Sender phone: {sender_phone}
Target buyer's recent purchase: {buyer_purchase_address} (bought for ${buyer_purchase_price:,.0f})

The email should:
- Reference their recent purchase as evidence you researched them specifically,
  not a mass blast
- State plainly that you source off-market wholesale deals and want to know
  if they'd like to be added to your buyer list for future deals matching
  their criteria
- Ask directly: what's their buy box (price range, areas, property condition,
  how fast they can close)?
- Include one line near the end: "Reply STOP or UNSUBSCRIBE at any time to
  be removed from future emails."
- No pressure, no hype words, no "amazing opportunity" language

Output ONLY the email text, no preamble, no markdown.
"""


def prospect_cash_buyers(org_id: str, county: str, state: str, days_back: int = 90,
                          limit: int = 50) -> int:
    """
    Pulls recent cash-sale deed records (proven buyers, not self-reported
    interest) and inserts them into `buyers` as unverified prospects.
    Verify BatchData's actual endpoint/field names for deed/sales records
    at developer.batchdata.com before relying on this -- same caveat as
    batchdata_service.py.
    """
    import httpx
    headers = {"Authorization": f"Bearer {os.environ['BATCHDATA_API_KEY']}",
               "Content-Type": "application/json"}
    payload = {
        "searchCriteria": {
            "query": f"{county} County, {state}",
            "saleType": "cash",
            "daysBack": days_back,
        },
        "options": {"take": limit},
    }
    with httpx.Client(timeout=30.0) as c:
        resp = c.post("https://api.batchdata.com/api/v1/property/sales-history",
                      json=payload, headers=headers)
        resp.raise_for_status()
        sales = resp.json().get("results", {}).get("sales", [])

    inserted = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for sale in sales:
                buyer_entity = sale.get("buyerName")
                if not buyer_entity:
                    continue
                cur.execute("""
                    INSERT INTO buyers (org_id, name, company, buy_box,
                                         closed_deals_count, last_closed_at, notes)
                    VALUES (%s, %s, %s, %s, 1, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (
                    org_id,
                    buyer_entity, buyer_entity,
                    json.dumps({"last_purchase_address": sale.get("address"),
                                "last_purchase_price": sale.get("salePrice")}),
                    sale.get("saleDate"),
                    f"Auto-prospected from cash sale deed record on {sale.get('address')}",
                ))
                inserted += 1

    return inserted


def draft_buyer_intro(buyer_id: str, sender_name: str, sender_company: str,
                       sender_phone: str) -> str:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM buyers WHERE id = %s", (buyer_id,))
            buyer = cur.fetchone()

    if not buyer:
        raise ValueError(f"Buyer {buyer_id} not found")

    buy_box = buyer.get("buy_box") or {}
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{"role": "user", "content": INTRO_PROMPT.format(
            sender_name=sender_name, sender_company=sender_company,
            sender_phone=sender_phone,
            buyer_purchase_address=buy_box.get("last_purchase_address", "a recent property"),
            buyer_purchase_price=buy_box.get("last_purchase_price", 0),
        )}],
    )
    return response.content[0].text.strip()
