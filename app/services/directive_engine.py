"""
Directive engine. Same conceptual pattern as CDAI's nightly directive system:
a deterministic set of rules scans current state and outputs a small list of
things that actually need YOUR attention today, instead of you reading every
row in every table.

Directive types (deliberately a fixed, known set -- add new types here AND
update the type list in schema comments together, same discipline as CDAI's
VALID_DIRECTIVE_ACTIONS):

  APPROVE_OFFER       - offer drafted, waiting on your review/approval
  STALE_LEAD          - scored lead with no contact attempt in 5+ days
  INSPECTION_DEADLINE - under-contract deal, inspection deadline within 3 days
  CLOSING_DEADLINE    - deal has a closing deadline within 5 days, no assigned buyer yet
  DEAL_STALLED        - deal with no activity in 7+ days, not closed/dead
  HIGH_SCORE_UNCONTACTED - a lead scored 80+ that's still sitting at status='scored'
  WIRE_FRAUD_VERIFICATION - deal nearing closing, mandatory wire-fraud reminder
  TAX_WITHHOLDING_REMINDER - deal just closed, set aside taxes reminder
  FORECLOSURE_ATTORNEY_CHECK - lis-pendens lead, offer drafting hard-blocked

This runs once a day via scripts/run_daily_brief.py (Render cron job), but
you can also hit POST /directives/generate manually any time.

KNOWN LIMITATION: delete-then-regenerate per org is not safe against two
concurrent runs for the same org (e.g. an overlapping cron + manual
trigger) -- both could delete, then both insert, doubling directives. Low
risk at solo-operator volume; revisit with a lock/upsert approach before
this runs at real concurrency.
"""

from datetime import datetime, timedelta
from app.db import get_conn

STALE_LEAD_DAYS = 5
DEAL_STALLED_DAYS = 7
INSPECTION_WARNING_DAYS = 3
CLOSING_WARNING_DAYS = 5
HIGH_SCORE_THRESHOLD = 80


def _insert_directive(cur, org_id, directive_type, priority, message,
                       deal_id=None, lead_id=None):
    cur.execute("""
        INSERT INTO directives (org_id, deal_id, lead_id, directive_type, priority, message)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (org_id, deal_id, lead_id, directive_type, priority, message))


def generate_directives(org_id: str) -> int:
    """
    Clears unresolved directives from prior runs for this org (so you don't
    get duplicate nags for the same thing) and regenerates fresh ones based
    on current state. Returns count of directives generated.
    """
    count = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Clear stale unresolved directives before regenerating --
            # avoids duplicate/stacking nags across days
            cur.execute("DELETE FROM directives WHERE org_id = %s AND resolved = FALSE", (org_id,))

            # --- APPROVE_OFFER: anything pending review, any age ---
            cur.execute("""
                SELECT o.id, o.offer_amount, l.address, o.created_at
                FROM offers o JOIN leads l ON l.id = o.lead_id
                WHERE l.org_id = %s AND o.approval_status = 'pending_review'
            """, (org_id,))
            for row in cur.fetchall():
                _insert_directive(cur, org_id, "APPROVE_OFFER", "urgent",
                    f"Offer of ${row['offer_amount']:,.0f} on {row['address']} is drafted "
                    f"and waiting on your review/approval before it can send.",
                    lead_id=None)
                count += 1

            # --- WIRE_FRAUD_VERIFICATION: any deal nearing closing gets a
            # mandatory reminder -- BEC/wire fraud targeting real estate
            # closings is a growing risk category (verify current stats
            # before quoting a specific figure to anyone). Never act on
            # wire instructions from email alone. ---
            cur.execute("""
                SELECT d.id, l.address, d.closing_deadline
                FROM deals d JOIN leads l ON l.id = d.lead_id
                WHERE d.org_id = %s AND d.stage IN ('assigned', 'marketing_to_buyers')
                  AND d.closing_deadline <= %s
            """, (org_id, (datetime.utcnow() + timedelta(days=7)).date()))
            for row in cur.fetchall():
                _insert_directive(cur, org_id, "WIRE_FRAUD_VERIFICATION", "urgent",
                    f"{row['address']} is closing soon. REMINDER: verify any wire "
                    f"instructions by calling the title company on a number YOU looked up "
                    f"independently -- never from an email, even if it looks legitimate.",
                    deal_id=row["id"])
                count += 1

            # --- TAX_WITHHOLDING_REMINDER: fires once per newly closed deal ---
            cur.execute("""
                SELECT d.id, l.address, d.assignment_fee
                FROM deals d JOIN leads l ON l.id = d.lead_id
                WHERE d.org_id = %s AND d.stage = 'closed'
                  AND d.closed_at > now() - interval '3 days'
                  AND d.assignment_fee IS NOT NULL
            """, (org_id,))
            for row in cur.fetchall():
                set_aside = round((row["assignment_fee"] or 0) * 0.30, 2)
                _insert_directive(cur, org_id, "TAX_WITHHOLDING_REMINDER", "today",
                    f"Deal closed on {row['address']} -- ${row['assignment_fee']:,.0f} "
                    f"assignment fee. Set aside roughly ${set_aside:,.0f} (30%) for taxes "
                    f"NOW, before spending it. This is ordinary income plus self-employment tax.",
                    deal_id=row["id"])
                count += 1

            # --- FORECLOSURE_ATTORNEY_CHECK: hard-blocked at draft_offer()
            # now, this directive just surfaces WHY it's blocked ---
            cur.execute("""
                SELECT id, address FROM leads
                WHERE org_id = %s AND is_lis_pendens_filed = TRUE
                  AND status IN ('new', 'scored')
            """, (org_id,))
            for row in cur.fetchall():
                _insert_directive(cur, org_id, "FORECLOSURE_ATTORNEY_CHECK", "urgent",
                    f"{row['address']} has an active foreclosure notice. Offer drafting is "
                    f"HARD-BLOCKED on this lead until attorney_cleared_foreclosure is TRUE on "
                    f"your org record -- get that attorney conversation done to unblock it.",
                    lead_id=row["id"])
                count += 1

            # --- STALE_LEAD: scored, no activity in N+ days ---
            cutoff = datetime.utcnow() - timedelta(days=STALE_LEAD_DAYS)
            cur.execute("""
                SELECT l.id, l.address, l.last_activity_at, ls.score
                FROM leads l
                LEFT JOIN LATERAL (
                    SELECT score FROM lead_scores WHERE lead_id = l.id
                    ORDER BY scored_at DESC LIMIT 1
                ) ls ON true
                WHERE l.org_id = %s AND l.status = 'scored' AND l.last_activity_at < %s
            """, (org_id, cutoff))
            for row in cur.fetchall():
                _insert_directive(cur, org_id, "STALE_LEAD", "this_week",
                    f"{row['address']} (score: {row['score']}) has sat untouched for "
                    f"{STALE_LEAD_DAYS}+ days. Decide: pursue or mark dead.",
                    lead_id=row["id"])
                count += 1

            # --- HIGH_SCORE_UNCONTACTED: great lead, nothing done yet ---
            cur.execute("""
                SELECT l.id, l.address, ls.score
                FROM leads l
                JOIN LATERAL (
                    SELECT score FROM lead_scores WHERE lead_id = l.id
                    ORDER BY scored_at DESC LIMIT 1
                ) ls ON true
                WHERE l.org_id = %s AND l.status = 'scored' AND ls.score >= %s
            """, (org_id, HIGH_SCORE_THRESHOLD))
            for row in cur.fetchall():
                _insert_directive(cur, org_id, "HIGH_SCORE_UNCONTACTED", "today",
                    f"{row['address']} scored {row['score']}/100 -- one of your best leads "
                    f"right now and no offer has been drafted yet.",
                    lead_id=row["id"])
                count += 1

            # --- INSPECTION_DEADLINE: approaching, under contract ---
            warn_date = datetime.utcnow().date() + timedelta(days=INSPECTION_WARNING_DAYS)
            cur.execute("""
                SELECT d.id, l.address, d.inspection_deadline
                FROM deals d JOIN leads l ON l.id = d.lead_id
                WHERE d.org_id = %s AND d.stage = 'under_contract'
                  AND d.inspection_deadline <= %s
            """, (org_id, warn_date))
            for row in cur.fetchall():
                _insert_directive(cur, org_id, "INSPECTION_DEADLINE", "urgent",
                    f"Inspection deadline on {row['address']} is {row['inspection_deadline']} "
                    f"-- decide whether to proceed or exercise your contingency.",
                    deal_id=row["id"])
                count += 1

            # --- CLOSING_DEADLINE: approaching, no buyer assigned ---
            warn_date2 = datetime.utcnow().date() + timedelta(days=CLOSING_WARNING_DAYS)
            cur.execute("""
                SELECT d.id, l.address, d.closing_deadline
                FROM deals d JOIN leads l ON l.id = d.lead_id
                WHERE d.org_id = %s AND d.stage IN ('under_contract', 'marketing_to_buyers')
                  AND d.closing_deadline <= %s AND d.assigned_buyer_id IS NULL
            """, (org_id, warn_date2))
            for row in cur.fetchall():
                _insert_directive(cur, org_id, "CLOSING_DEADLINE", "urgent",
                    f"Closing deadline on {row['address']} is {row['closing_deadline']} and "
                    f"NO BUYER IS ASSIGNED YET. This needs attention now, not later today.",
                    deal_id=row["id"])
                count += 1

            # --- DEAL_STALLED: no activity, not closed/dead ---
            stalled_cutoff = datetime.utcnow() - timedelta(days=DEAL_STALLED_DAYS)
            cur.execute("""
                SELECT d.id, l.address, d.stage, d.last_activity_at
                FROM deals d JOIN leads l ON l.id = d.lead_id
                WHERE d.org_id = %s AND d.stage NOT IN ('closed', 'dead')
                  AND d.last_activity_at < %s
            """, (org_id, stalled_cutoff))
            for row in cur.fetchall():
                _insert_directive(cur, org_id, "DEAL_STALLED", "this_week",
                    f"{row['address']} (stage: {row['stage']}) has had no activity in "
                    f"{DEAL_STALLED_DAYS}+ days. Follow up or move on.",
                    deal_id=row["id"])
                count += 1

    return count


def get_active_directives(org_id: str) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM directives
                WHERE org_id = %s AND resolved = FALSE
                ORDER BY
                    CASE priority WHEN 'urgent' THEN 0 WHEN 'today' THEN 1 ELSE 2 END,
                    generated_at DESC
            """, (org_id,))
            return cur.fetchall()


def resolve_directive(directive_id: str) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE directives SET resolved = TRUE, resolved_at = now()
                WHERE id = %s RETURNING *
            """, (directive_id,))
            return cur.fetchone()
