"""
Matches a deal (under-contract property) against your buyer list's stated
buy_box criteria, ranked by fit. Purely a matching/ranking function -- sending
the actual marketing email to matched buyers uses gmail_service.send_email(),
same human-review pattern recommended (buyers are less legally sensitive than
seller cold outreach, but still worth a quick human glance before sending).
"""

from app.db import get_conn


def match_buyers_for_deal(org_id: str, deal_id: str, top_n: int = 10) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT d.*, l.address, l.zip, l.county, l.estimated_value
                FROM deals d JOIN leads l ON l.id = d.lead_id
                WHERE d.id = %s AND d.org_id = %s
            """, (deal_id, org_id))
            deal = cur.fetchone()

            cur.execute("SELECT * FROM buyers WHERE org_id = %s", (org_id,))
            buyers = cur.fetchall()

    if not deal:
        raise ValueError(f"Deal {deal_id} not found")

    price = deal["contract_price"] or deal["estimated_value"] or 0
    county = deal["county"]

    scored = []
    for buyer in buyers:
        buy_box = buyer.get("buy_box") or {}
        score = 0

        price_min = buy_box.get("price_min", 0)
        price_max = buy_box.get("price_max", float("inf"))
        if price_min <= price <= price_max:
            score += 40

        buyer_counties = buy_box.get("counties", [])
        if not buyer_counties or (county and county in buyer_counties):
            score += 30

        if buyer["closed_deals_count"] and buyer["closed_deals_count"] > 0:
            score += min(buyer["closed_deals_count"], 20)  # proven closers rank higher

        # NOTE: institutional buyer bonus REMOVED as of the 21st Century ROAD
        # to Housing Act (2026), which bars "large institutional investors"
        # (350+ SFH under control) from purchasing single-family homes going
        # forward. The exact buyer category this used to favor is now largely
        # legally blocked from new acquisitions. Local/regional cash buyers
        # and smaller investors under the 350-home threshold are now your
        # REAL primary buyer market, not a fallback while you build toward
        # institutional relationships. Verify current status of this law
        # before assuming any specific institutional buyer is still active --
        # don't hardcode a bonus back in without checking first.
        if score > 0:
            scored.append({**buyer, "match_score": score})

    scored.sort(key=lambda b: b["match_score"], reverse=True)
    return scored[:top_n]
