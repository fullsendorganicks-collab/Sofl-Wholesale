"""
Generates and sends your daily brief: runs the directive engine, formats
everything into a readable email, and fires a short SMS summary if there's
anything urgent. This is the single feature that makes the whole system
usable while you're solo -- you should never need to log in and dig through
tables to know what needs your attention today.
"""

from datetime import datetime
from app.db import get_conn
from app.services import directive_engine, gmail_service, sms_service

PRIORITY_LABELS = {"urgent": "URGENT", "today": "TODAY", "this_week": "THIS WEEK"}


def _format_brief_email(org_name: str, directives: list[dict], stats: dict) -> tuple[str, str]:
    urgent = [d for d in directives if d["priority"] == "urgent"]
    today = [d for d in directives if d["priority"] == "today"]
    this_week = [d for d in directives if d["priority"] == "this_week"]

    subject = f"Wholesale Brief - {datetime.now().strftime('%b %d')} - "
    if urgent:
        subject += f"{len(urgent)} URGENT item(s)"
    elif directives:
        subject += f"{len(directives)} item(s) need attention"
    else:
        subject += "all clear"

    lines = [f"Daily brief for {org_name} - {datetime.now().strftime('%A, %B %d, %Y')}", ""]
    lines.append(f"Pipeline snapshot: {stats['new_leads']} new leads | "
                  f"{stats['scored_leads']} scored | {stats['active_deals']} active deals | "
                  f"{stats['pending_offers']} offers awaiting your review")
    lines.append("")

    if not directives:
        lines.append("Nothing needs your attention today. Pipeline is quiet.")
    for label, group in [("URGENT - act today", urgent),
                          ("TODAY", today),
                          ("THIS WEEK", this_week)]:
        if not group:
            continue
        lines.append(f"\n{label}")
        lines.append("-" * len(label))
        for d in group:
            lines.append(f"* [{d['directive_type']}] {d['message']}")

    lines.append("\n\n---")
    lines.append("This brief was generated automatically. Review pending offers before "
                  "approving -- nothing sends without your explicit sign-off.")

    return subject, "\n".join(lines)


def generate_and_send_brief(org_id: str, org_name: str, contact_email: str,
                             contact_phone: str | None = None) -> dict:
    directive_engine.generate_directives(org_id)
    directives = directive_engine.get_active_directives(org_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) as c FROM leads WHERE org_id=%s AND status='new'", (org_id,))
            new_leads = cur.fetchone()["c"]
            cur.execute("SELECT count(*) as c FROM leads WHERE org_id=%s AND status='scored'", (org_id,))
            scored_leads = cur.fetchone()["c"]
            cur.execute("SELECT count(*) as c FROM deals WHERE org_id=%s AND stage NOT IN ('closed','dead')", (org_id,))
            active_deals = cur.fetchone()["c"]
            cur.execute("""
                SELECT count(*) as c FROM offers o JOIN leads l ON l.id=o.lead_id
                WHERE l.org_id=%s AND o.approval_status='pending_review'
            """, (org_id,))
            pending_offers = cur.fetchone()["c"]

    stats = {"new_leads": new_leads, "scored_leads": scored_leads,
             "active_deals": active_deals, "pending_offers": pending_offers}

    subject, body = _format_brief_email(org_name, directives, stats)
    message_id = gmail_service.send_email(contact_email, subject, body)

    urgent_count = len([d for d in directives if d["priority"] == "urgent"])
    sms_sid = None
    if contact_phone and urgent_count > 0:
        sms_body = (f"Wholesale Agent: {urgent_count} URGENT item(s) today. "
                    f"Check email for details.")
        sms_sid = sms_service.send_sms(contact_phone, sms_body)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO notification_log (org_id, channel, subject, body, directive_count)
                VALUES (%s, 'email', %s, %s, %s)
            """, (org_id, subject, body, len(directives)))
            if sms_sid:
                cur.execute("""
                    INSERT INTO notification_log (org_id, channel, subject, body, directive_count)
                    VALUES (%s, 'sms', %s, %s, %s)
                """, (org_id, "urgent summary", sms_body, urgent_count))

    return {"directive_count": len(directives), "urgent_count": urgent_count,
            "email_sent": True, "sms_sent": sms_sid is not None}
