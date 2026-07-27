from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv(override=True)  # .env always wins over stale shell/session env vars

from app.services import (batchdata_service, lead_scoring, deal_analysis,
                           offer_drafting, gmail_service, buyer_matching,
                           directive_engine, daily_brief, buyer_acquisition,
                           contract_generation)
from app.db import get_conn

app = FastAPI(title="Wholesale Agent API")


# ---------------------------------------------------------------
# LEADS
# ---------------------------------------------------------------
class IngestRequest(BaseModel):
    org_id: str
    county: str
    state: str
    zip_codes: list[str] | None = None
    filters: dict | None = None
    limit: int = 100


@app.post("/leads/ingest")
def ingest_leads(req: IngestRequest):
    count = batchdata_service.ingest_leads(req.org_id, req.county, req.state,
                                            req.zip_codes, req.filters, req.limit)
    return {"inserted": count}


@app.post("/leads/score-all")
def score_all():
    count = lead_scoring.score_all_new_leads()
    return {"scored": count}


@app.get("/leads")
def list_leads(org_id: str, status: str | None = None, min_score: float | None = None):
    query = """
        SELECT l.*, ls.score, ls.reasoning
        FROM leads l
        LEFT JOIN LATERAL (
            SELECT score, reasoning FROM lead_scores
            WHERE lead_id = l.id ORDER BY scored_at DESC LIMIT 1
        ) ls ON true
        WHERE l.org_id = %s
    """
    params = [org_id]
    if status:
        query += " AND l.status = %s"
        params.append(status)
    if min_score is not None:
        query += " AND ls.score >= %s"
        params.append(min_score)
    query += " ORDER BY ls.score DESC NULLS LAST LIMIT 200"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()


# ---------------------------------------------------------------
# DEAL ANALYSIS
# ---------------------------------------------------------------
class AnalyzeRequest(BaseModel):
    lead_id: str
    estimated_repairs: float
    target_assignment_fee: float | None = None
    manual_arv: float | None = None


@app.post("/deals/analyze")
def analyze(req: AnalyzeRequest):
    try:
        result = deal_analysis.analyze_deal(
            req.lead_id, req.estimated_repairs,
            req.target_assignment_fee or 12000, req.manual_arv,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------
# OFFERS  (draft -> human review -> approve -> send)
# ---------------------------------------------------------------
class DraftOfferRequest(BaseModel):
    lead_id: str
    offer_amount: float
    sender_name: str
    sender_phone: str


@app.post("/offers/draft")
def draft(req: DraftOfferRequest):
    """Creates a DRAFT ONLY. Nothing is sent. Read the draft_letter field,
    then call /offers/{id}/approve if it looks right."""
    try:
        return offer_drafting.draft_offer(req.lead_id, req.offer_amount,
                                           req.sender_name, req.sender_phone)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/offers/pending")
def pending_offers():
    """Everything waiting on YOUR review. Check this list before anything goes out."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT o.*, l.address, l.owner_name
                FROM offers o JOIN leads l ON l.id = o.lead_id
                WHERE o.approval_status = 'pending_review'
                ORDER BY o.created_at DESC
            """)
            return cur.fetchall()


class ApproveRequest(BaseModel):
    approved_by: str


@app.post("/offers/{offer_id}/approve")
def approve(offer_id: str, req: ApproveRequest):
    try:
        return offer_drafting.approve_offer(offer_id, req.approved_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/offers/{offer_id}/reject")
def reject(offer_id: str):
    return offer_drafting.reject_offer(offer_id)


@app.post("/offers/{offer_id}/send")
def send(offer_id: str):
    """Only works if the offer's approval_status is already 'approved'."""
    try:
        message_id = gmail_service.send_approved_offer(offer_id)
        return {"sent": True, "gmail_message_id": message_id}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------
# BUYERS / DEALS
# ---------------------------------------------------------------
@app.get("/deals/{deal_id}/matched-buyers")
def matched_buyers(deal_id: str, org_id: str, top_n: int = 10):
    try:
        return buyer_matching.match_buyers_for_deal(org_id, deal_id, top_n)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------
# DIRECTIVES ("what do I need to do today")
# ---------------------------------------------------------------
@app.post("/directives/generate")
def generate_directives(org_id: str):
    count = directive_engine.generate_directives(org_id)
    return {"generated": count}


@app.get("/directives")
def list_directives(org_id: str):
    """Same list that shows up in your daily brief -- pull this any time
    you want a live view rather than waiting for the scheduled email."""
    return directive_engine.get_active_directives(org_id)


@app.post("/directives/{directive_id}/resolve")
def resolve_directive(directive_id: str):
    return directive_engine.resolve_directive(directive_id)


# ---------------------------------------------------------------
# DAILY BRIEF (manual trigger -- normally runs via Render Cron Job,
# see scripts/run_daily_brief.py)
# ---------------------------------------------------------------
class BriefRequest(BaseModel):
    org_id: str
    org_name: str
    contact_email: str
    contact_phone: str | None = None


@app.post("/brief/send-now")
def send_brief_now(req: BriefRequest):
    """Manually trigger a brief send -- useful for testing before you
    trust the scheduled cron job, or if you want an on-demand refresh."""
    return daily_brief.generate_and_send_brief(
        req.org_id, req.org_name, req.contact_email, req.contact_phone,
    )


# ---------------------------------------------------------------
# BUYER ACQUISITION (the revenue-critical piece -- do this FIRST,
# not last, per the plan in CLAUDE_CODE_BUILD_PROMPT.md)
# ---------------------------------------------------------------
class ProspectBuyersRequest(BaseModel):
    org_id: str
    county: str
    state: str
    days_back: int = 90
    limit: int = 50


@app.post("/buyers/prospect")
def prospect_buyers(req: ProspectBuyersRequest):
    count = buyer_acquisition.prospect_cash_buyers(req.org_id, req.county, req.state,
                                                     req.days_back, req.limit)
    return {"prospected": count}


class BuyerIntroRequest(BaseModel):
    buyer_id: str
    sender_name: str
    sender_company: str
    sender_phone: str


@app.post("/buyers/{buyer_id}/draft-intro")
def draft_buyer_intro(buyer_id: str, req: BuyerIntroRequest):
    draft = buyer_acquisition.draft_buyer_intro(buyer_id, req.sender_name,
                                                  req.sender_company, req.sender_phone)
    return {"draft": draft}


# ---------------------------------------------------------------
# CONTRACT GENERATION (template-based, not LLM-generated -- see
# contract_generation.py for why. Both hard-gated on
# attorney_cleared_general_wholesaling being TRUE on the org.)
# ---------------------------------------------------------------
class PurchaseAgreementRequest(BaseModel):
    org_id: str
    deal_id: str
    seller_name: str
    buyer_name: str
    purchase_price: float
    emd_amount: float
    title_company_name: str
    inspection_days: int = 10
    closing_days_out: int = 25


@app.post("/contracts/purchase-agreement")
def create_purchase_agreement(req: PurchaseAgreementRequest):
    try:
        return contract_generation.generate_purchase_agreement(
            req.org_id, req.deal_id, req.seller_name, req.buyer_name,
            req.purchase_price, req.emd_amount, req.title_company_name,
            req.inspection_days, req.closing_days_out,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class AssignmentAgreementRequest(BaseModel):
    org_id: str
    deal_id: str
    assignor_name: str
    assignee_name: str
    assignment_fee: float
    title_company_name: str
    original_contract_date: str


@app.post("/contracts/assignment-agreement")
def create_assignment_agreement(req: AssignmentAgreementRequest):
    try:
        return contract_generation.generate_assignment_agreement(
            req.org_id, req.deal_id, req.assignor_name, req.assignee_name,
            req.assignment_fee, req.title_company_name, req.original_contract_date,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/health")
def health():
    return {"status": "ok"}
