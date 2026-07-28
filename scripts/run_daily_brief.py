"""
This is what Render's Cron Job service calls once a day (see README.md
"Cron Job Setup"). Loops through every org in the system and sends each
their brief -- this is the multi-tenant-ready part: if you license this
out later, one cron job serves every customer, nothing per-client to add.
"""

import os
import sys

# Render's Cron Job runs this script directly (not via `python -m`), so the
# project root isn't automatically on sys.path the way it is for the Web
# Service's `uvicorn app.main:app` invocation -- add it explicitly or the
# `app` package import below fails with ModuleNotFoundError.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(override=True)  # .env always wins over stale shell/session env vars

from app.db import get_conn
from app.services import daily_brief


def run():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, contact_email, contact_phone FROM orgs")
            orgs = cur.fetchall()

    for org in orgs:
        try:
            result = daily_brief.generate_and_send_brief(
                org["id"], org["name"], org["contact_email"], org["contact_phone"],
            )
            print(f"[{org['name']}] Brief sent: {result}")
        except Exception as e:
            print(f"[{org['name']}] FAILED to send brief: {e}")


if __name__ == "__main__":
    run()
