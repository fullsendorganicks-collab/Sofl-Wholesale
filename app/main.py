import os
import hmac
import secrets
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
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
# DASHBOARD AUTH -- a single shared password, not a full user system.
# Good enough for a solo operator; revisit if this ever has more than
# one person logging in. Session tokens are opaque random strings held
# server-side in memory (fine for one instance; would need a shared
# store like Redis if this ever scales to multiple server processes).
# ---------------------------------------------------------------
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD")
_valid_sessions: set[str] = set()


def _check_session(request: Request) -> None:
    token = request.cookies.get("session")
    if not token or token not in _valid_sessions:
        raise HTTPException(status_code=401, detail="Not logged in")


class LoginRequest(BaseModel):
    password: str


@app.post("/api/login")
def login(req: LoginRequest, response: Response):
    if not DASHBOARD_PASSWORD or not hmac.compare_digest(req.password, DASHBOARD_PASSWORD):
        raise HTTPException(status_code=401, detail="Incorrect password")
    token = secrets.token_urlsafe(32)
    _valid_sessions.add(token)
    response.set_cookie("session", token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("session")
    if token:
        _valid_sessions.discard(token)
    response.delete_cookie("session")
    return {"ok": True}


@app.get("/api/session")
def check_session(request: Request):
    token = request.cookies.get("session")
    return {"logged_in": bool(token and token in _valid_sessions)}


# ---------------------------------------------------------------
# DASHBOARD DATA -- one combined endpoint so the frontend doesn't have
# to make five separate calls to render the main view.
# ---------------------------------------------------------------
@app.get("/api/dashboard")
def dashboard_data(request: Request, org_id: str):
    _check_session(request)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT l.*, ls.score, ls.reasoning
                FROM leads l
                LEFT JOIN LATERAL (
                    SELECT score, reasoning FROM lead_scores
                    WHERE lead_id = l.id ORDER BY scored_at DESC LIMIT 1
                ) ls ON true
                WHERE l.org_id = %s
                ORDER BY ls.score DESC NULLS LAST, l.created_at DESC
                LIMIT 100
            """, (org_id,))
            leads = cur.fetchall()

            cur.execute("""
                SELECT o.*, l.address, l.owner_name, l.owner_email
                FROM offers o JOIN leads l ON l.id = o.lead_id
                WHERE l.org_id = %s
                ORDER BY o.created_at DESC
                LIMIT 50
            """, (org_id,))
            offers = cur.fetchall()

            cur.execute("""
                SELECT * FROM buyers WHERE org_id = %s ORDER BY created_at DESC LIMIT 100
            """, (org_id,))
            buyers = cur.fetchall()

            cur.execute("""
                SELECT count(*) as c FROM leads WHERE org_id=%s AND status='new'
            """, (org_id,))
            new_leads = cur.fetchone()["c"]
            cur.execute("""
                SELECT count(*) as c FROM leads WHERE org_id=%s AND status='scored'
            """, (org_id,))
            scored_leads = cur.fetchone()["c"]
            cur.execute("""
                SELECT count(*) as c FROM deals WHERE org_id=%s AND stage NOT IN ('closed','dead')
            """, (org_id,))
            active_deals = cur.fetchone()["c"]
            cur.execute("""
                SELECT count(*) as c FROM offers o JOIN leads l ON l.id=o.lead_id
                WHERE l.org_id=%s AND o.approval_status='pending_review'
            """, (org_id,))
            pending_offers = cur.fetchone()["c"]

    directives = directive_engine.get_active_directives(org_id)

    return {
        "leads": leads,
        "offers": offers,
        "buyers": buyers,
        "directives": directives,
        "stats": {
            "new_leads": new_leads, "scored_leads": scored_leads,
            "active_deals": active_deals, "pending_offers": pending_offers,
        },
    }


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


class ManualLeadRequest(BaseModel):
    org_id: str
    address: str
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    county: str | None = None
    owner_name: str | None = None
    owner_phone: str | None = None
    owner_email: str | None = None
    estimated_value: float | None = None
    equity_estimate: float | None = None
    distress_signals: list[str] = []
    is_lis_pendens_filed: bool = False


@app.post("/leads/manual")
def add_manual_lead(req: ManualLeadRequest, request: Request):
    """
    Add a lead by hand -- for when BatchData isn't funded/available, or
    for a property you already know about personally. Runs through the
    exact same scoring/offer/compliance pipeline as a BatchData-sourced
    lead; the source field just says 'manual' instead of 'batchdata'.
    """
    _check_session(request)
    import json as _json
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO leads (org_id, source, address, city, state, zip, county,
                                    owner_name, owner_phone, owner_email, estimated_value,
                                    equity_estimate, distress_signals, is_lis_pendens_filed, status)
                VALUES (%s,'manual',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'new')
                RETURNING *
            """, (req.org_id, req.address, req.city, req.state, req.zip, req.county,
                  req.owner_name, req.owner_phone, req.owner_email, req.estimated_value,
                  req.equity_estimate, _json.dumps(req.distress_signals), req.is_lis_pendens_filed))
            return cur.fetchone()


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
def draft(req: DraftOfferRequest, request: Request):
    """Creates a DRAFT ONLY. Nothing is sent. Read the draft_letter field,
    then call /offers/{id}/approve if it looks right."""
    _check_session(request)
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
def approve(offer_id: str, req: ApproveRequest, request: Request):
    _check_session(request)
    try:
        return offer_drafting.approve_offer(offer_id, req.approved_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/offers/{offer_id}/reject")
def reject(offer_id: str, request: Request):
    _check_session(request)
    return offer_drafting.reject_offer(offer_id)


@app.post("/offers/{offer_id}/send")
def send(offer_id: str, request: Request):
    """Only works if the offer's approval_status is already 'approved'."""
    _check_session(request)
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


class ManualBuyerRequest(BaseModel):
    org_id: str
    name: str
    company: str | None = None
    email: str | None = None
    phone: str | None = None
    price_min: float | None = None
    price_max: float | None = None
    counties: list[str] = []
    notes: str | None = None


@app.post("/buyers/manual")
def add_manual_buyer(req: ManualBuyerRequest, request: Request):
    """Add a cash buyer by hand -- for buyers you already know personally,
    or while BatchData's Investor Buy Box isn't funded/available."""
    _check_session(request)
    import json as _json
    buy_box = {}
    if req.price_min is not None:
        buy_box["price_min"] = req.price_min
    if req.price_max is not None:
        buy_box["price_max"] = req.price_max
    if req.counties:
        buy_box["counties"] = req.counties

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO buyers (org_id, name, company, email, phone, buy_box, notes)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                RETURNING *
            """, (req.org_id, req.name, req.company, req.email, req.phone,
                  _json.dumps(buy_box), req.notes))
            return cur.fetchone()


@app.get("/buyers")
def list_buyers(org_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM buyers WHERE org_id = %s ORDER BY created_at DESC", (org_id,))
            return cur.fetchall()


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


# ---------------------------------------------------------------
# STATIC DASHBOARD -- serves app/static/index.html and its assets.
# Mounted last so it never shadows an API route above.
# ---------------------------------------------------------------
_static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
