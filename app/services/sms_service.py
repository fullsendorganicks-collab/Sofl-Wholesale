"""
SMS notifications via Twilio. Used only for the daily brief summary and
urgent same-day alerts -- NOT for contacting sellers or buyers. This sends
to YOU (the operator), not to leads, which is a materially different TCPA
posture than consumer marketing texts -- but if SMS/calling to sellers or
buyers is ever added later, that needs its own deliberate consent-capture
and opt-out layer. Do not extend this module to message leads without
building that layer first.

Sign up at twilio.com, buy a phone number (~$1/month + usage, pennies per SMS).
"""

import os
from twilio.rest import Client

TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM_NUMBER = os.environ["TWILIO_FROM_NUMBER"]

client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def send_sms(to_number: str, body: str) -> str:
    """SMS bodies should stay short -- this is for the urgent summary line,
    not the full brief (that goes to email). Returns the Twilio message SID."""
    message = client.messages.create(body=body, from_=TWILIO_FROM_NUMBER, to=to_number)
    return message.sid
