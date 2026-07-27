"""
Generates the actual Purchase & Sale Agreement and Assignment of Contract
Agreement -- the paperwork that makes a wholesale deal real. This was the
single biggest gap flagged in review: offer_drafting.py produces a letter,
not a contract, and the whole business model depends on an assignable
purchase agreement.

DELIBERATE DESIGN CHOICE: these are TEMPLATES with fields filled in via
plain string formatting, NOT Claude-generated text. A legal contract should
have fixed, attorney-reviewed language -- letting an LLM freestyle contract
wording per-generation (even a good one) introduces exactly the kind of
variability that has no place in binding legal paperwork. Draft the
template ONCE, get your attorney to actually review THAT template, set
attorney_reviewed=TRUE once, then every generated document reuses the same
reviewed language with only the data fields changing.

HARD REQUIREMENTS, both enforced in code below, not just documentation:
1. attorney_cleared_general_wholesaling must be TRUE on the org before ANY
   contract document generates for real use -- same gate offer_drafting.py
   enforces. A reviewed contract template doesn't matter if the underlying
   question of whether you're legally allowed to operate as a principal
   buyer at all is still unresolved.
2. attorney_reviewed must be TRUE on the specific document_type's template
   before it generates un-watermarked. Until then, output is clearly
   watermarked "UNREVIEWED TEMPLATE" so you can see what it looks like
   without mistaking it for something safe to actually sign.
"""

from datetime import date
from app.db import get_conn

PURCHASE_AGREEMENT_TEMPLATE = """
PURCHASE AND SALE AGREEMENT

DISCLAIMER: This is a general-purpose educational template, not legal, tax, or
financial advice. We are not attorneys. Real estate law varies by state. Have
this document reviewed and customized by a licensed real estate attorney in
the state where the property is located before signing or using it.

This Agreement ("Agreement") is entered into as of {effective_date}
("Effective Date") between {seller_name} ("Seller") and {buyer_name}
and/or assigns ("Buyer").

1. Property
Seller agrees to sell and Buyer agrees to purchase the real property located
at {property_address} (the "Property"), together with all improvements and
fixtures.

2. Purchase Price
The total purchase price shall be ${purchase_price:,.2f}, payable in cash at
Closing.

3. Earnest Money
Buyer shall deposit ${emd_amount:,.2f} with {title_company_name} ("Title
Company") in escrow within 3 business days of the Effective Date. Earnest
Money shall be applied to the Purchase Price at Closing or refunded to
Buyer if this Agreement is terminated under the Inspection Period or any
other contingency below.

4. Inspection Period
Buyer shall have {inspection_days} days from the Effective Date to inspect
the Property. Buyer may terminate this Agreement for ANY REASON during this
period by written notice to Seller, with Earnest Money refunded in full.

5. Assignability
Buyer may assign this Agreement, in whole or in part, to any third party
without Seller's further consent. This Agreement and all rights herein
shall inure to the benefit of Buyer's assigns.

6. Closing
Closing shall occur on or before {closing_date}, at {title_company_name},
unless extended by mutual written agreement of the parties.

7. Disclosure
Buyer is purchasing this Property for investment purposes and may resell
or assign its interest in this contract prior to Closing. Buyer is not
acting as a licensed real estate broker in this transaction.

SIGNATURES

Seller: _________________________  Date: __________

Buyer: {buyer_name} and/or assigns

By: _________________________  Date: __________
""".strip()

ASSIGNMENT_AGREEMENT_TEMPLATE = """
ASSIGNMENT OF REAL ESTATE PURCHASE AND SALE AGREEMENT

DISCLAIMER: This is a general-purpose educational template, not legal, tax,
or financial advice. We are not attorneys. Have this document reviewed and
customized by a licensed real estate attorney before signing or using it.

This Assignment Agreement ("Assignment") is entered into as of
{effective_date} between {assignor_name} ("Assignor") and {assignee_name}
("Assignee").

1. Underlying Contract
Assignor is the Buyer under a Purchase and Sale Agreement dated
{original_contract_date} for the property located at {property_address}
(the "Underlying Contract"), entered into with {seller_name} as Seller.

2. Assignment
Assignor hereby assigns, transfers, and conveys to Assignee all of
Assignor's right, title, and interest in and to the Underlying Contract,
including the right to purchase the Property, subject to the terms and
conditions of the Underlying Contract.

3. Assignment Fee
In consideration for this Assignment, Assignee shall pay Assignor an
assignment fee of ${assignment_fee:,.2f}, payable at Closing through the
settlement statement prepared by {title_company_name}.

4. Assignee's Obligations
Assignee accepts all obligations of the Buyer under the Underlying
Contract, including the obligation to close in accordance with its terms.

SIGNATURES

Assignor: {assignor_name}

By: _________________________  Date: __________

Assignee: {assignee_name}

By: _________________________  Date: __________
""".strip()


def get_template_config(org_id: str) -> dict:
    """
    Returns {document_type: attorney_reviewed} using the SINGLE latest row
    per document_type, not a blanket LIMIT 2 across both types. A blanket
    LIMIT 2 could return two rows of the SAME document_type if several were
    generated back-to-back while testing -- silently dropping the other
    type from the dict and, worse, letting a later reviewed document
    regress to "unreviewed" watermarking even after your attorney actually
    approved that template. DISTINCT ON guarantees exactly one row per
    document_type, the most recent one.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (document_type) document_type, attorney_reviewed
                FROM contract_documents
                WHERE org_id = %s AND document_type IN ('purchase_agreement', 'assignment_agreement')
                ORDER BY document_type, generated_at DESC
            """, (org_id,))
            return {r["document_type"]: r["attorney_reviewed"] for r in cur.fetchall()}


def _require_wholesaling_clearance(org_id: str) -> None:
    """
    HARD GATE, same one offer_drafting.py enforces: a reviewed contract
    template doesn't matter if attorney_cleared_general_wholesaling is
    still FALSE for this org. Without this check, contract generation
    could produce real, non-watermarked purchase agreements while the
    underlying question of whether you're legally allowed to operate as
    a principal buyer at all is still unresolved.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT attorney_cleared_general_wholesaling FROM orgs WHERE id = %s", (org_id,))
            org = cur.fetchone()
    if not org:
        raise ValueError(f"Org {org_id} not found")
    if not org.get("attorney_cleared_general_wholesaling"):
        raise PermissionError(
            "attorney_cleared_general_wholesaling is FALSE for your org. Contract documents "
            "will not generate until a Florida real estate attorney confirms you qualify for "
            "the principal-buyer exemption under 475.011 -- same gate offer_drafting.py "
            "enforces. Update the orgs table once done."
        )


def generate_purchase_agreement(org_id: str, deal_id: str, seller_name: str,
                                 buyer_name: str, purchase_price: float,
                                 emd_amount: float, title_company_name: str,
                                 inspection_days: int = 10,
                                 closing_days_out: int = 25) -> dict:
    _require_wholesaling_clearance(org_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT l.address FROM deals d JOIN leads l ON l.id = d.lead_id
                WHERE d.id = %s AND d.org_id = %s
            """, (deal_id, org_id))
            row = cur.fetchone()
    if not row:
        raise ValueError(f"Deal {deal_id} not found for this org")

    from datetime import timedelta
    effective = date.today()
    closing = effective + timedelta(days=closing_days_out)

    text = PURCHASE_AGREEMENT_TEMPLATE.format(
        effective_date=effective.isoformat(), seller_name=seller_name,
        buyer_name=buyer_name, property_address=row["address"],
        purchase_price=purchase_price, emd_amount=emd_amount,
        title_company_name=title_company_name, inspection_days=inspection_days,
        closing_date=closing.isoformat(),
    )

    reviewed = get_template_config(org_id).get("purchase_agreement", False)
    if not reviewed:
        text = "*** UNREVIEWED TEMPLATE -- DO NOT SIGN OR USE UNTIL YOUR ATTORNEY " \
               "HAS REVIEWED THIS EXACT TEMPLATE AND attorney_reviewed IS SET TRUE ***\n\n" + text

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO contract_documents (org_id, deal_id, document_type,
                                                 document_text, attorney_reviewed)
                VALUES (%s, %s, 'purchase_agreement', %s, %s)
                RETURNING *
            """, (org_id, deal_id, text, reviewed))
            return cur.fetchone()


def generate_assignment_agreement(org_id: str, deal_id: str, assignor_name: str,
                                   assignee_name: str, assignment_fee: float,
                                   title_company_name: str,
                                   original_contract_date: str) -> dict:
    _require_wholesaling_clearance(org_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT l.address FROM deals d JOIN leads l ON l.id = d.lead_id
                WHERE d.id = %s AND d.org_id = %s
            """, (deal_id, org_id))
            row = cur.fetchone()
    if not row:
        raise ValueError(f"Deal {deal_id} not found for this org")

    text = ASSIGNMENT_AGREEMENT_TEMPLATE.format(
        effective_date=date.today().isoformat(), assignor_name=assignor_name,
        assignee_name=assignee_name, original_contract_date=original_contract_date,
        property_address=row["address"], seller_name="[see underlying contract]",
        assignment_fee=assignment_fee, title_company_name=title_company_name,
    )

    reviewed = get_template_config(org_id).get("assignment_agreement", False)
    if not reviewed:
        text = "*** UNREVIEWED TEMPLATE -- DO NOT SIGN OR USE UNTIL YOUR ATTORNEY " \
               "HAS REVIEWED THIS EXACT TEMPLATE AND attorney_reviewed IS SET TRUE ***\n\n" + text

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO contract_documents (org_id, deal_id, document_type,
                                                 document_text, attorney_reviewed)
                VALUES (%s, %s, 'assignment_agreement', %s, %s)
                RETURNING *
            """, (org_id, deal_id, text, reviewed))
            return cur.fetchone()
