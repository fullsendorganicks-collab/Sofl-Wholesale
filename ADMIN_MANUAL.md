# SoFlo Wholesale — Admin Manual

Living reference doc. Update this whenever something changes (new account,
new key, new fix, new decision) — this is the single source of truth for
"what is this system and how do I operate it," not the chat history.

Last updated: 2026-07-28

---

## 1. What this is

An AI-assisted real estate wholesaling backend + dashboard. It sources
leads, scores them, drafts offer letters, tracks compliance gates, matches
buyers, and sends you a daily email brief of what needs attention. Every
outbound action (offer emails, contracts) requires your explicit approval
— nothing sends itself.

## 2. Where everything lives

| What | Where |
|---|---|
| Code (local) | `C:\Users\19196\wholesale-agent` |
| Code (GitHub) | https://github.com/fullsendorganicks-collab/Sofl-Wholesale |
| Database | Supabase, org "Soflo Wholesale", project ref `cavznviysciikqfmiykq` |
| Backend + Dashboard (live) | https://sofl-wholesale.onrender.com |
| Dashboard login | Password: see `.env` → `DASHBOARD_PASSWORD` (currently `Ktm250excSoflo`) |
| Daily brief cron | Render project "Soflo Wholesale" → Cron Job service, runs 11:00 UTC (7am ET) |
| Local secrets | `C:\Users\19196\wholesale-agent\.env` (never committed to git) |

**Your org_id**: `62a17f85-15df-4c49-aca1-c75a3a36c120`

## 3. Accounts & credentials checklist

| Service | Status | Notes |
|---|---|---|
| Supabase | ✅ Live | Password reset once already — if login ever fails, reset again via Project Settings → Database, avoid `$` characters (caused a real bug once) |
| Anthropic (Claude API) | ✅ Live | Separate key from CDAI, billed separately |
| Gmail OAuth | ✅ Live | Sends from `baumnicholas@gmail.com`. Refresh token in `.env` — if it ever expires (Google's "Testing" mode consent screens can expire tokens after 7 days if not moved to production), rerun `scripts/gmail_oauth_setup.py` |
| Render | ✅ Live | Two services under "Soflo Wholesale" project: Web Service + Cron Job, both need the same env vars |
| GitHub | ✅ Live | Private repo, `.env` and `client_secret.json` correctly gitignored |
| Twilio (SMS) | ❌ Not set up | Deliberately skipped — costs money, email brief covers everything. `sms_service.py` is fully optional, app runs fine without it |
| BatchData | ⚠️ Account created, NOT funded | Needs $50 minimum wallet balance before any lead/buyer sourcing works. API key is saved and confirmed reachable (got a real 403 "Insufficient balance", not a connection error) |

## 4. What's proven working (real end-to-end tests done)

- Lead scoring via Claude (real score + reasoning generated)
- Offer drafting via Claude, gated correctly behind attorney-clearance flags
- Compliance gate: proven to BLOCK when flag is False, proven to UNBLOCK when flipped True
- Gmail sending: daily brief emails received for real (both local and from Render's cron)
- Send-offer safety check: correctly refuses to send when a lead has no owner_email
- Web dashboard: real login, real session, real data, deployed publicly
- Both Render services (Web Service + Cron Job) deployed and functioning
- **Manual lead entry** (`POST /leads/manual`, "+ Add lead manually" button on dashboard) — tested end-to-end, inserts with `source='manual'`, flows through the same scoring/offer pipeline as any other lead
- **Manual buyer entry** (`POST /buyers/manual`, "+ Add buyer manually" button on dashboard) — tested end-to-end, feeds `buyer_matching.py` the same as a BatchData-prospected buyer

## 5. What's built but never tested with real data

- `batchdata_service.py` (lead ingestion) — endpoint reachable, field-name mapping unverified against a real successful response. Now includes a hard qualifying filter (rejects properties with zero distress signals or equity below `min_equity_percent`, default 30%) — filter logic itself is straightforward Python, but its real-world effect depends on BatchData's actual returned equity/value numbers, still unverified
- `buyer_acquisition.py` (buyer prospecting) — same, untested
- `deal_analysis.py` (ARV/MAO calculator) — logic only, no real comps run through it
- `contract_generation.py` (Purchase Agreement + Assignment Agreement) — never actually generated a real document
- `buyer_matching.py` — logic untested against a real deal, though buyers can now be added manually to test it without BatchData
- Most directive types (`STALE_LEAD`, `CLOSING_DEADLINE`, `WIRE_FRAUD_VERIFICATION`, etc.) — never fired because no deal has ever existed

## 6. What's not built at all

- No deal has ever moved past "offer drafted" — the under-contract → closing pipeline is unexercised, and there's no dashboard UI yet for creating a `deals` row or progressing its stage
- `attorney_cleared_foreclosure` is still `FALSE` — correctly blocking any lis-pendens/foreclosure lead until that specific attorney conversation is confirmed
- No dashboard button for contract generation yet (`contract_generation.py` exists and is API-reachable, just not wired into the UI)

## 4b. Manual data entry (until BatchData is funded)

BatchData needs a $50 minimum wallet balance (see §3) that isn't funded
yet, so **manual entry is the primary way real data enters this system
right now** — not a fallback. Both are on the dashboard:

- **"+ Add lead manually"** under the Leads section — property address,
  owner contact, estimated value/equity, distress signals. Runs through
  the identical scoring → offer → compliance pipeline as a BatchData lead.
- **"+ Add buyer manually"** under the Buyers section — name, contact,
  buy-box price range and counties. Feeds `buyer_matching.py` exactly
  like a BatchData-prospected buyer would.

Use these freely for any property or buyer you already know about
personally — no cost, no BatchData dependency.

## 7. Compliance flags (orgs table)

| Flag | Current value | What it blocks |
|---|---|---|
| `attorney_cleared_general_wholesaling` | `TRUE` | Blocks ALL offer drafting + contract generation until true (confirmed set 2026-07-27) |
| `attorney_cleared_foreclosure` | `FALSE` | Blocks offer drafting on ANY lis-pendens/foreclosure lead, regardless of deal structure |

**Do not flip `attorney_cleared_foreclosure` to TRUE until a real Florida
attorney has specifically confirmed your letter/contract structure against
FL Statute 501.1377.** This is a separate conversation from the general
wholesaling clearance.

## 8. Known issues / things to watch

- **Render free tier spins down when idle** — first request after inactivity can take 30-50s to respond. Not a bug, just the free-tier tradeoff.
- **Dashboard password**: copy-pasting it sometimes fails (invisible characters from clipboard) — type manually if login fails unexpectedly.
- **BatchData needs $50 minimum** to fund the wallet before any real lead/buyer data can be pulled.

## 9. Next steps, in priority order

1. Use manual lead/buyer entry (§4b) to keep working the pipeline while BatchData is unfunded
2. Fund BatchData ($50 minimum) when there's budget — unlocks automated lead + buyer sourcing
3. Run one real `POST /leads/ingest` call, verify field mapping matches what BatchData actually returns (per original build plan's Rule 3 — don't trust the code's assumptions blindly)
4. Run one real `POST /buyers/prospect` call, same verification
5. Get the foreclosure-specific attorney confirmation, flip `attorney_cleared_foreclosure`
6. Build a dashboard UI for creating/progressing a `deals` row once an offer gets a real "yes"
7. Add a contract-generation button to the dashboard
8. Consider Twilio only if SMS urgency alerts become genuinely needed (not required for the system to work)

## 10. How to update this file

Whenever something changes — a new key added, a bug found and fixed, a
decision made about scope — add it here immediately, dated. This file
should always reflect the actual current state, not a snapshot from when
it was written.
