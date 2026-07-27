"""
Calculates ARV from comps and the Maximum Allowable Offer (MAO) using the
standard 70% rule:

    MAO = (ARV * 0.70) - estimated_repairs - target_assignment_fee

Comps currently need to be supplied manually or pulled from BatchData's
comps endpoint (check developer.batchdata.com for the current comps/AVM
endpoint path -- this varies by plan tier). For a first build, entering
comps manually (e.g. from an agent contact pulling MLS comps for you) is
completely fine and arguably more reliable than an automated AVM.
"""

import os
from app.db import get_conn

ARV_MULTIPLIER = float(os.environ.get("ARV_MULTIPLIER", "0.70"))
DEFAULT_ASSIGNMENT_FEE = float(os.environ.get("DEFAULT_ASSIGNMENT_FEE_TARGET", "12000"))


def calculate_arv_from_comps(lead_id: str) -> float | None:
    """Simple average of comp sale prices. Replace with a weighted/adjusted
    model later if you want more sophistication (sqft-adjusted, distance-weighted)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT sale_price FROM comps WHERE lead_id = %s", (lead_id,))
            rows = cur.fetchall()

    prices = [r["sale_price"] for r in rows if r["sale_price"]]
    if not prices:
        return None
    return sum(prices) / len(prices)


def calculate_mao(arv: float, estimated_repairs: float,
                   target_assignment_fee: float = DEFAULT_ASSIGNMENT_FEE) -> float:
    return round((arv * ARV_MULTIPLIER) - estimated_repairs - target_assignment_fee, 2)


def analyze_deal(lead_id: str, estimated_repairs: float,
                  target_assignment_fee: float = DEFAULT_ASSIGNMENT_FEE,
                  manual_arv: float | None = None) -> dict:
    arv = manual_arv or calculate_arv_from_comps(lead_id)
    if arv is None:
        raise ValueError(f"No ARV available for lead {lead_id} -- add comps first or pass manual_arv.")

    mao = calculate_mao(arv, estimated_repairs, target_assignment_fee)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO deal_analysis (lead_id, arv, estimated_repairs,
                                            target_assignment_fee, mao)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING *
            """, (lead_id, arv, estimated_repairs, target_assignment_fee, mao))
            result = cur.fetchone()

    return result
