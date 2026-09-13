# Sofl-Wholesale

Backend for a solo-operator real estate wholesaling business. Sources distressed-property leads, scores and analyzes deals, drafts offers, matches buyers, and generates contracts — with a daily automated brief so nothing falls through the cracks.

Built and run as production infrastructure for a real operation, not a demo — see [ADMIN_MANUAL.md](ADMIN_MANUAL.md) for the actual incident history (a real billing overrun, root-caused and fixed; a mis-parsed API field, caught by a safeguard instead of failing silently).

## What it does

- **Lead sourcing** — pulls distressed-property leads from [BatchData](https://batchdata.com)'s property/owner API, with a hard per-call record ceiling and a near-zero-cost preview endpoint (both added after a real cost incident, documented in the admin manual).
- **Lead scoring & deal analysis** — scores incoming leads and runs ARV-based deal analysis to flag viable opportunities.
- **Offer drafting & contract generation** — automates the paperwork once a deal looks worth pursuing.
- **Buyer acquisition & matching** — builds and matches against a buyer list for assignment deals.
- **Directive engine** — a deterministic daily scan (same design pattern as [Allocera CDAI](https://alloceraintelligence.com)'s directive system) that surfaces exactly what needs attention today: stale leads, approaching inspection/closing deadlines, stalled deals, wire-fraud verification reminders — instead of requiring a manual read of every row in every table.
- **Daily brief** — a scheduled cron job (Render) that emails/texts (Gmail API + Twilio) a daily operational summary.

## Stack

Python · FastAPI · PostgreSQL (Supabase) · BatchData API · Gmail API · Twilio

## Architecture notes

- Single shared dashboard password, not a full multi-user auth system — a deliberate choice for a solo operator, called out explicitly in code rather than left implicit (`app/main.py`).
- Session tokens are opaque, server-held, in-memory — fine at one instance, documented as needing a shared store (e.g. Redis) before scaling to multiple processes.
- The directive engine's delete-then-regenerate approach is explicitly documented as not safe against concurrent runs — a known, accepted limitation at current volume, not an oversight.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in Supabase, BatchData, Google, Twilio credentials
uvicorn app.main:app --reload
```

See `.env.example` for the full list of required credentials and where to get each one.
