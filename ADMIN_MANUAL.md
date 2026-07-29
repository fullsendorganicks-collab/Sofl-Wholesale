# SoFlo Wholesale — Admin Manual

Living reference doc. Update this whenever something changes (new account,
new key, new fix, new decision) — this is the single source of truth for
"what is this system and how do I operate it," not the chat history.

Last updated: 2026-07-29, early hours (full cost incident resolved, root
cause confirmed, real fix built — NOT yet pushed/deployed/tested)

**Current status: core loop proven working. A real, serious cost incident
happened tonight, fully explained now via BatchData support + a real
itemized ledger pull, and a real fix is written but sitting local-only,
untested against the live API. Read this whole section before touching
BatchData again.**

### What actually happened (full, confirmed accounting)

Wallet went $50.00 → $28.10 (~$21.90 spent) across ~9 API calls on
2026-07-28. **Root cause, confirmed via BatchData support: `/property/search`
is NOT free.** Earlier code/docs in this project incorrectly stated
"Property Search Sessions: $0.00" based on a misreading of BatchData's
pricing page. In reality, search is billed **per record returned** (not
per call) — confirmed via a real itemized consumption-report pull
(`GET /api/v1/wallet/consumption-report`):

| Time | Endpoint | Records requested | Cost |
|---|---|---|---|
| 4:26 PM | wallet top-up | — | +$50.00 |
| 4:27–4:31 PM | search ×4 | small batches | $1.92, $0.64, $0.64, $1.92 |
| 5:11 PM | search | — | $1.92 |
| 5:11 PM | skip-trace | 1 property | $0.07 |
| 5:27 PM | search | — | $1.92 |
| 5:27 PM | skip-trace | 1 property | $0.07 |
| **11:24 PM** | **search, `limit: 20`** | **20 records** | **$12.80** |

The $12.80 charge was a "dry_run" test explicitly described (incorrectly,
by me) as free — dry_run only skips `skip_trace()` and the DB insert, it
never skipped the actual paid search call. **skip-trace pricing ($0.07)
was accurate the whole time** — only search pricing was wrong.

### The real fix (written 2026-07-29, NOT yet pushed or tested)

- `MAX_SEARCH_RECORDS_PER_CALL = 10` — hard ceiling enforced *inside*
  `search_distressed_properties()`, cannot be bypassed by any caller
- Corrected docstrings everywhere — no more "search is free" claims
- **`count_matching_properties()`** (new function) + **`POST /leads/preview-count`**
  (new endpoint) — confirmed via BatchData support: sending `take: 0`
  returns a match count billed as only ONE record at the dataset rate.
  This is the real near-zero-cost way to check "how many properties
  match this filter" before running a real search. **Use this before
  every new county/quicklists combination**, not the old `dry_run` (which
  still costs money — misleadingly named, kept for backward compat but
  don't treat it as free)
- **Server-side filtering fixed**: BatchData support confirmed the correct
  mechanism is a `quickLists` array of string tags (`"absentee-owner"`,
  `"tax-default"`, `"preforeclosure"`, `"notice-of-default"`,
  `"high-equity"`) combined with AND logic — NOT the flat top-level
  booleans (`{"absenteeOwner": true}`) the code used before, which
  support confirmed is not a supported filter and silently did nothing.
  `QUICKLIST_TAG_MAP` in `batchdata_service.py` translates our internal
  signal names to BatchData's real tags. Confirmed: price per record does
  NOT vary by which quickLists filter is used — it's a flat dataset rate,
  filtering only reduces how many (irrelevant) records you pay for.

### What's NOT done yet

This fix has been written and syntax-checked, and the server boots
cleanly locally — but **has not been pushed to GitHub, not deployed to
Render, and not tested against a real BatchData call.** Next session:
review the diff, push, deploy, then test `count_matching_properties()`
first (near-zero cost) before any real search/ingest call.

**Rule going forward, permanently**: call `POST /leads/preview-count`
first on any new county/quicklists combination. Never call `/leads/ingest`
"to see what happens" — state the exact worst-case cost first and get
explicit approval before every real BatchData call, no exceptions.

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
| BatchData | ✅ Live, funded | $50 paid 2026-07-28, balance $28.10 as of end of session (full incident explained — see status note at top). **Property search is NOT free** — billed per record returned, price does not vary by quickLists filter used. Skip trace confirmed accurate at $0.07/property. Real near-zero-cost preview: `POST /leads/preview-count` (take:0, billed as ~1 record). `/leads/ingest` hard-caps search to 10 records/call and skip-trace via `max_skip_traces` (default 5, ~$0.35 max) — fix written 2026-07-29, not yet deployed. |

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
- **BatchData lead ingestion, full pipeline** (`POST /leads/ingest`) — tested end-to-end with real funded credits, 2026-07-28. Real property (2632 NE 29th Ct, Fort Lauderdale) searched → qualifying filter correctly rejected 2/3 properties (no distress signal or insufficient equity) → 1 passed → skip-traced → inserted with correct owner name + mailing address → scored by Claude (32/100, correctly identified as weak — see below). First fully-automated real lead in the system.
- **Two-tier qualifying filter** (tightened 2026-07-28 after reviewing that 32/100 lead) — `tax_delinquent`, `pre_foreclosure`, and `lis_pendens_filed` qualify at 30% equity (unchanged); `absentee_owner` ALONE now requires 50% equity (`WEAK_SIGNAL_MIN_EQUITY_PERCENT` in `batchdata_service.py`), since an absentee landlord isn't necessarily a motivated seller. Real dry-run test on 20 properties: 11 would qualify, 6 rejected (no signal / strong-signal-but-low-equity), 3 rejected specifically for weak-signal-insufficient-equity (`rejected_weak_signal_insufficient_equity` in the response) — confirms the new tier is actually distinguishing weak vs. strong signals, not just theory
- **Spending cap + dry-run mode** — `max_skip_traces` caps skip-trace spend per `/leads/ingest` call (default 5 ≈ $0.35 max in skip-trace fees). `dry_run: true` skips skip-trace + the DB insert, but ⚠️ **still runs the real, paid search call** — it is NOT zero cost (see status note at top for the real $12.80 incident this caused). Response includes `skip_traces_used` and `estimated_cost_usd`, but that estimate covers skip-trace only, not search — use `POST /leads/preview-count` for a genuinely near-zero-cost check

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
- **Server-side filtering**: use `quickLists` array of string tags (`"absentee-owner"`, `"tax-default"`, `"preforeclosure"`, `"notice-of-default"`, `"high-equity"`), AND logic. Flat top-level booleans like `{"absenteeOwner": true}` are NOT a supported filter (confirmed by BatchData support 2026-07-29) and silently do nothing — this was the original bug. The client-side qualifying filter in `ingest_leads()` still re-checks every property regardless, since quickLists narrows the pool but doesn't replace the strong/weak-signal equity logic
- **Pricing**: search billed per record returned, price does NOT vary by which quickLists filter is applied (flat dataset rate). `take: 0` returns a count only, billed as ~1 record — the real near-zero-cost preview mechanism (see status note at top)

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
- **Real cost incident, 2026-07-28, FULLY EXPLAINED**: $21.90 spent, root cause was incorrectly believing `/property/search` was free when it's actually billed per record returned (confirmed via BatchData support + real consumption-report pull — see status note at top for the exact itemized breakdown). Fix (10-record hard cap + `count_matching_properties()` near-zero-cost preview) is written but NOT yet deployed as of end of session. Do not deploy without re-reading the status note at the top of this file first.

## 10. Next steps, in priority order

1. **Review, push, and deploy the cost fix** written 2026-07-29 (10-record search cap, `count_matching_properties()`, corrected `quickLists` filtering) — currently local-only
2. **First real call after deploying**: `POST /leads/preview-count` on a real county/quicklists combination (near-zero cost) — confirm it returns a sane number before ever calling `/leads/ingest` again
3. **In progress**: post-scoring quality gate in `lead_scoring.py` — leads scoring below 50 should get `status='low_priority'` instead of `'scored'`, hidden from the default dashboard view but not deleted (deferred to a later session, per plan agreed 2026-07-28 — this is "Step 2" of a two-step plan; Step 1, the ingestion-time weak-signal filter, is done)
4. Resolve skip-trace phone/email extraction — check Render logs for the `[batchdata_service] skip_trace: unrecognized results shape, keys=[...]` line on the next real call to see the actual structure
5. Run one real `POST /buyers/prospect` call — verify field mapping the same way search/skip-trace needed, and check whether it has the same quickLists/pricing gaps search did before assuming otherwise
6. Get the foreclosure-specific attorney confirmation, flip `attorney_cleared_foreclosure`
7. Build a dashboard UI for creating/progressing a `deals` row once an offer gets a real "yes"
8. Add a contract-generation button to the dashboard
9. Consider Twilio only if SMS urgency alerts become genuinely needed (not required for the system to work)

## 11. How to update this file

Whenever something changes — a new key added, a bug found and fixed, a
decision made about scope — add it here immediately, dated. This file
should always reflect the actual current state, not a snapshot from when
it was written.
