"""
SMS notifications via Twilio. Used only for the daily brief summary and
urgent same-day alerts -- NOT for contacting sellers or buyers. This sends
to YOU (the operator), not to leads, which is a materially different TCPA
posture than consumer marketing texts -- but if SMS/calling to sellers or
buyers is ever added later, that needs its own deliberate consent-capture
and opt-out layer. Do not extend this module to message leads without
building that layer first.

OPTIONAL: Twilio is not required to run this app. If TWILIO_ACCOUNT_SID is
unset, send_sms() becomes a no-op that logs instead of sending -- the daily
brief already only calls this when an org has contact_phone set (see
daily_brief.py), so email-only orgs are unaffected either way.

Sign up at twilio.com, buy a phone number (~$1/month + usage, pennies per SMS).
"""

import os

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER")

_client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER:
    from twilio.rest import Client
    _client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def send_sms(to_number: str, body: str) -> str | None:
    """SMS bodies should stay short -- this is for the urgent summary line,
    not the full brief (that goes to email). Returns the Twilio message SID,
    or None if Twilio isn't configured (no-op, not an error)."""
    if _client is None:
        print(f"[sms_service] Twilio not configured, skipping SMS to {to_number}: {body}")
        return None
    message = _client.messages.create(body=body, from_=TWILIO_FROM_NUMBER, to=to_number)
    return message.sid
