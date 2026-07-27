"""
Gmail API integration. Sends approved offers via your Gmail account and can
poll for replies. Uses OAuth2 with a long-lived refresh token (generated once
via scripts/gmail_oauth_setup.py -- see README.md "Gmail Setup").

This only ever sends an email after an offer's approval_status == 'approved'
-- see send_approved_offer() below. It will not send anything still in
'pending_review'.
"""

import os
import base64
from email.mime.text import MIMEText
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from app.db import get_conn

GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
GOOGLE_REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"]
GMAIL_SENDER_ADDRESS = os.environ["GMAIL_SENDER_ADDRESS"]

SCOPES = ["https://www.googleapis.com/auth/gmail.send",
          "https://www.googleapis.com/auth/gmail.readonly"]


def _get_gmail_client():
    creds = Credentials(
        token=None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    return build("gmail", "v1", credentials=creds)


def send_email(to_address: str, subject: str, body_text: str) -> str:
    """Sends a plain-text email via the connected Gmail account.
    Returns the Gmail message ID."""
    service = _get_gmail_client()

    message = MIMEText(body_text)
    message["to"] = to_address
    message["from"] = GMAIL_SENDER_ADDRESS
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return sent["id"]


def send_approved_offer(offer_id: str) -> str:
    """
    THE gate: only sends if approval_status == 'approved'. This function is
    intentionally the ONLY path that sends an offer email, so there's exactly
    one place in the whole codebase where outbound contact happens -- easy to
    audit, easy to disable if you ever want a stricter human-in-the-loop step.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT o.*, l.owner_email, l.address
                FROM offers o JOIN leads l ON l.id = o.lead_id
                WHERE o.id = %s
            """, (offer_id,))
            offer = cur.fetchone()

    if not offer:
        raise ValueError(f"Offer {offer_id} not found")
    if offer["approval_status"] != "approved":
        raise PermissionError(
            f"Offer {offer_id} has status '{offer['approval_status']}', not 'approved'. "
            "Call approve_offer() first -- this is intentional, not a bug."
        )
    if not offer["owner_email"]:
        raise ValueError(f"No owner email on file for lead {offer['lead_id']} -- "
                          "this offer needs to go out by mail instead.")

    subject = f"Regarding {offer['address']}"
    message_id = send_email(offer["owner_email"], subject, offer["draft_letter"])

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE offers SET approval_status = 'sent', sent_at = now(),
                                   gmail_message_id = %s
                WHERE id = %s
            """, (message_id, offer_id))
            cur.execute("""
                INSERT INTO communications (lead_id, direction, channel, subject,
                                             body, gmail_message_id)
                VALUES (%s, 'outbound', 'email', %s, %s, %s)
            """, (offer["lead_id"], subject, offer["draft_letter"], message_id))

    return message_id
