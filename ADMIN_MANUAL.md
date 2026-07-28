# SoFlo Wholesale — Admin Manual

Living reference doc. Update this whenever something changes (new account,
new key, new fix, new decision) — this is the single source of truth for
"what is this system and how do I operate it," not the chat history.

Last updated: 2026-07-28 (evening — BatchData funded and tested for real)

**Current status: core loop proven working end-to-end with REAL data.**
BatchData is funded ($50 paid) and `/leads/ingest` successfully pulled,
qualified, skip-traced, and inserted a real property lead (2632 NE 29th
Ct, Fort Lauderdale — real owner name + mailing address, real equity/value
numbers), which then scored correctly via Claude. The field-name mapping
that was previously unverified (§6) is now fixed and confirmed correct
for search results and owner name/mailing address.

**One real gap remains**: owner phone/email extraction from skip-trace is
still unconfirmed — the endpoint returns *something* without crashing,
but not in a shape the code recognizes as phone/email yet (see §6). This
means offers can be drafted and mailed (real mailing address works) but
not yet auto-emailed via Gmail on BatchData-sourced leads specifically,
until that's resolved.

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
| BatchData | ✅ Live, funded | $50 paid 2026-07-28. Property search confirmed FREE (Property Search Sessions = $0.00/request per their pricing page). Skip trace costs ~$0.07/property, only runs on properties that pass the qualifying filter. One real test run: 3 properties searched, 2 rejected by the filter (correctly), 1 inserted + skip-traced (~$0.07 spent total so far) |

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
- **BatchData lead ingestion, full pipeline** (`POST /leads/ingest`) — tested end-to-end with real funded credits, 2026-07-28. Real property (2632 NE 29th Ct, Fort Lauderdale) searched → qualifying filter correctly rejected 2/3 properties (no distress signal or insufficient equity) → 1 passed → skip-traced → inserted with correct owner name + mailing address → scored by Claude. First fully-automated real lead in the system.
- **Qualifying filter** — proven to actually reject: 2 of 3 real search results rejected for lacking a real distress signal or falling below the 30% equity threshold, not just scored low

## 5. Manual data entry (until BatchData is funded)

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

## 6. BatchData field mapping — what's confirmed vs. still open

Real, hard-won findings from getting `/leads/ingest` working tonight —
read this before touching `batchdata_service.py` again:

**Confirmed correct** (verified against real API responses, 2026-07-28):
- `/property/search` response: distress signals live under `quickLists.{taxDefault,absenteeOwner,preforeclosure}`, NOT flat top-level fields
- Foreclosure/lis-pendens: `foreclosure.status` contains `"Notice of Lis Pendens"` when applicable (the `foreclosure` key may be absent/empty otherwise)
- Valuation: `valuation.{estimatedValue,equityCurrentEstimatedBalance,equityPercent}`
- **Owner name + mailing address**: `prop["owner"].{fullName,mailingAddress}` — already present in the search response itself, no skip-trace needed for these two fields
- Passing `{"absenteeOwner": true}` etc. in the search request does NOT reliably filter results server-side (confirmed: 2 of 3 results had `absenteeOwner: false` despite the filter) — the client-side qualifying filter in `ingest_leads()` is what actually enforces criteria, not BatchData's request-side filter

**Still unconfirmed / best-effort:**
- `skip_trace()`'s actual response shape for phone/email. The one real call didn't crash (defensive fallback matched something) but didn't expose phone/email in a recognized field either — `owner_phone`/`owner_email` come back `null` on the one real lead so far. Code currently tries a few plausible field-name guesses; next real skip-trace call's outcome (or a `[batchdata_service] skip_trace: unrecognized...` log line in Render) will tell us the real shape
- `buyer_acquisition.py` (`/property/sales-history` endpoint, buyer prospecting) — completely untested, likely has the same kind of field-mapping gap search/skip-trace both had
- `deal_analysis.py` (ARV/MAO calculator) — logic only, no real comps run through it
- `contract_generation.py` — never actually generated a real document
- `buyer_matching.py` — logic untested against a real deal (buyers table still empty of real entries)
- Most directive types (`STALE_LEAD`, `CLOSING_DEADLINE`, `WIRE_FRAUD_VERIFICATION`, etc.) — never fired, no deal has ever existed

## 7. What's not built at all

- No deal has ever moved past "offer drafted" — the under-contract → closing pipeline is unexercised, and there's no dashboard UI yet for creating a `deals` row or progressing its stage
- `attorney_cleared_foreclosure` is still `FALSE` — correctly blocking any lis-pendens/foreclosure lead until that specific attorney conversation is confirmed
- No dashboard button for contract generation yet (`contract_generation.py` exists and is API-reachable, just not wired into the UI)
- Dashboard lead cards don't surface most of the rich data BatchData actually returns (tax history, listing history, demographics, lien info) — it's all sitting in `raw_payload`, just not displayed yet

## 8. Compliance flags (orgs table)

| Flag | Current value | What it blocks |
|---|---|---|
| `attorney_cleared_general_wholesaling` | `TRUE` | Blocks ALL offer drafting + contract generation until true (confirmed set 2026-07-27) |
| `attorney_cleared_foreclosure` | `FALSE` | Blocks offer drafting on ANY lis-pendens/foreclosure lead, regardless of deal structure |

**Do not flip `attorney_cleared_foreclosure` to TRUE until a real Florida
attorney has specifically confirmed your letter/contract structure against
FL Statute 501.1377.** This is a separate conversation from the general
wholesaling clearance.

## 9. Known issues / things to watch

- **Render free tier spins down when idle** — first request after inactivity can take 30-50s to respond. Not a bug, just the free-tier tradeoff.
- **Dashboard password**: copy-pasting it sometimes fails (invisible characters from clipboard) — type manually if login fails unexpectedly.
- **Render deploys can silently serve stale files** — happened once with the dashboard HTML (Render said "Deploy live" but served an old version). If a deploy looks live but the change isn't showing, use Manual Deploy → "Clear build cache & deploy" rather than a normal redeploy.
- **BATCHDATA_API_KEY must be set on the Web Service specifically** — it was missing there once even though it was in `.env` locally, causing a `RuntimeError` on every ingest call. Check Render's Environment tab on the Web Service (not just the Cron Job) if ingestion ever fails with a "not set" error.
- **Owner phone/email from skip-trace are not yet reliable** — see §6. Mailing address and owner name work; phone/email are best-effort until the real response shape is confirmed.

## 10. Next steps, in priority order

1. Run a larger real `/leads/ingest` batch (10-50 properties, not just 3) to confirm the pipeline holds up at volume, not just on one lucky match
2. Resolve skip-trace phone/email extraction — check Render logs for the `[batchdata_service] skip_trace: unrecognized results shape, keys=[...]` line on the next real call to see the actual structure
3. Run one real `POST /buyers/prospect` call, verify field mapping the same way search/skip-trace needed (per original build plan's Rule 3 — don't trust the code's assumptions blindly)
4. Get the foreclosure-specific attorney confirmation, flip `attorney_cleared_foreclosure`
5. Build a dashboard UI for creating/progressing a `deals` row once an offer gets a real "yes"
6. Add a contract-generation button to the dashboard
7. Surface more of BatchData's rich data (tax history, listing history) on the lead card — currently only in `raw_payload`
8. Consider Twilio only if SMS urgency alerts become genuinely needed (not required for the system to work)

## 11. How to update this file

Whenever something changes — a new key added, a bug found and fixed, a
decision made about scope — add it here immediately, dated. This file
should always reflect the actual current state, not a snapshot from when
it was written.
