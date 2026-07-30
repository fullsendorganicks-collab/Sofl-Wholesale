# SoFlo Wholesale — Admin Manual

Living reference doc. Update this whenever something changes (new account,
new key, new fix, new decision) — this is the single source of truth for
"what is this system and how do I operate it," not the chat history.

Last updated: 2026-07-29, ~7:30 PM — cost fix deployed, count-preview bug
found and fixed, real $8.67 test completed, dashboard "Find New Leads"
panel built and live

**Current status: BatchData pipeline is deployed, working, and its real
per-lead cost is now known from an actual test — $8.67 for 5 inserted
leads (~$1.73/lead). Read this whole section before running another real
ingest.**

### Incident #1 (2026-07-28 night): search wrongly believed free

Wallet went $50.00 → $28.10 (~$21.90 spent) across ~9 calls. Root cause,
confirmed via BatchData support + a real consumption-report pull:
`/property/search` is billed **per record returned**, not free as earlier
project docs incorrectly claimed. Full itemized breakdown:

| Time | Endpoint | Records | Cost |
|---|---|---|---|
| 4:26 PM | wallet top-up | — | +$50.00 |
| 4:27–4:31 PM | search ×4 | small batches | $1.92, $0.64, $0.64, $1.92 |
| 5:11 PM | search + skip-trace | 1 property | $1.92 + $0.07 |
| 5:27 PM | search + skip-trace | 1 property | $1.92 + $0.07 |
| 11:24 PM | search, `limit: 20` | 20 records | $12.80 |

**Fix deployed** (commit `dfa47e2`, 2026-07-29): `MAX_SEARCH_RECORDS_PER_CALL = 10`
hard ceiling inside `search_distressed_properties()`; `count_matching_properties()`
+ `POST /leads/preview-count` (a `take: 0` request, confirmed by BatchData
support to bill as ~1 record — the real near-zero-cost preview); corrected
`quickLists` server-side filtering (see below); all docstrings fixed.

### Incident #2 (2026-07-29 evening): count field name wrong, then confirmed

`count_matching_properties()` shipped with 3 guessed field names for the
match count (`meta.total`, `totalCount`, `total`) — none were real.
**The safeguard worked as designed**: instead of silently returning a
wrong `0`, it logged the raw response and returned `count: None`, visibly
telling the user it couldn't parse the count rather than lying. Real
response captured from Render logs, fix deployed (commit `8b7e93b`):

```
Real field: results.meta.results.resultsFound
Confirmed against 2 real responses:
  - {resultCount: 0, resultsFound: 0}       -> unfiltered query, 0 real matches
  - {resultCount: 0, resultsFound: 731805}  -> Palm Beach County, no filter, total properties
```

### First real ingest test after both fixes (2026-07-29, ~7 PM)

`county: "palm beach"` via the new dashboard "Find New Leads" panel →
**Inserted: 5, rejected: 1, skip-traces used: 5.**

**Real cost, confirmed by checking the actual BatchData wallet balance
before/after** (the response itself only reports skip-trace cost, not
search cost — this is a known gap, see §9): **$28.10 → $19.43 = $8.67
spent for 5 inserted leads (~$1.73/lead).** Confirmed skip-trace portion:
5 × $0.07 = $0.35. The remaining ~$8.32 was the search call itself
(returned up to 10 records at BatchData's per-record rate).

**Business math discussed**: `DEFAULT_ASSIGNMENT_FEE_TARGET = 12000` (a
typical wholesale assignment fee). Even at a pessimistic 1-in-20 lead-to-close
rate, $1.73/lead × 20 = $34.60 spent to generate one ~$12,000 deal — the
economics plausibly work. **The real optimization opportunity is reducing
search cost per lead** (tighter quicklists/county targeting so fewer
irrelevant records get returned and billed), not avoiding BatchData
entirely.

### Dashboard: "Find New Leads" panel (built + deployed, commit `6ba0402`)

Two-step UI in the Leads section: (1) "Check How Many Leads Match" —
calls `/leads/preview-count` only, shows real count + a cost warning; (2)
a separate "Pull Up To N Leads Now" button appears only after step 1,
requires a browser `confirm()` dialog before calling the real, paid
`/leads/ingest`. No single click can trigger real spending.

**Current BatchData wallet balance: $19.43** (as of 2026-07-29, ~7:30 PM).

**Rule going forward, permanently**: check the actual wallet balance
before and after any new real ingest call to confirm the true cost — the
`/leads/ingest` response's `estimated_cost_usd` field only covers
skip-trace, NOT search, so it understates real cost. State expected
worst-case cost before every real BatchData call.

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
| BatchData | ✅ Live, funded, working | $50 paid 2026-07-28. **Balance: $19.43** as of 2026-07-29 ~7:30 PM, after a real, successful 5-lead ingest (~$1.73/lead, see status note). Property search billed per record returned (NOT free); skip trace confirmed accurate at $0.07/property. Preview: `POST /leads/preview-count` (take:0, ~1 record cost, real count field confirmed: `results.meta.results.resultsFound`). `/leads/ingest` hard-caps search to 10 records/call, skip-trace via `max_skip_traces` (default 5, ~$0.35 max skip-trace cost — search cost is separate and NOT included in the response). Dashboard "Find New Leads" panel live and working. |

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
- **BatchData lead ingestion, full pipeline, TWO real successful runs**:
  - 2026-07-28: 1 lead (2632 NE 29th Ct, Fort Lauderdale) — owner name + mailing address correct, scored 32/100 by Claude (correctly identified as weak: single soft signal, high value, no real urgency)
  - 2026-07-29: 5 leads inserted from Palm Beach County via the new dashboard panel, real cost $8.67 confirmed via wallet balance check (see status note at top)
- **Two-tier qualifying filter** — `tax_delinquent`, `pre_foreclosure`, `lis_pendens_filed` qualify at 30% equity; `absentee_owner` ALONE requires 50% equity (`WEAK_SIGNAL_MIN_EQUITY_PERCENT`), since an absentee landlord isn't necessarily motivated to sell below market. Confirmed distinguishing weak vs. strong signals correctly in a real dry-run test (11 qualify / 6 rejected / 3 rejected-weak-signal out of 20)
- **Server-side quickLists filtering** — confirmed correct mechanism via BatchData support (array of tags like `"tax-default"`, `"absentee-owner"`, AND logic), replacing the original flat-boolean approach that silently filtered nothing
- **Near-zero-cost count preview** — `POST /leads/preview-count`, confirmed real field name (`resultsFound`), verified against 2 real responses (0 and 731,805)
- **Spending cap** — `MAX_SEARCH_RECORDS_PER_CALL = 10` (search), `max_skip_traces` default 5 (~$0.35 max skip-trace fees) — both enforced server-side, cannot be bypassed by a caller
- **Dashboard "Find New Leads" panel** — two-step UI (check count → explicit confirm before spending), live and used for the successful 2026-07-29 5-lead test

## 5. Manual data entry (still useful even with BatchData working)

BatchData is funded and working (see §3), but manual entry stays useful
for any property/buyer you already know personally — no cost, instant,
same downstream pipeline. Both are on the dashboard:

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
- **Render deploys can silently serve stale files** — happened multiple times tonight (both API code and dashboard HTML). Render says "Deploy live" but browser/server can still show old content. Fix: Manual Deploy → "Clear build cache & deploy" on Render's side, AND a hard refresh (Ctrl+Shift+R) in the browser — both caches have caused this independently.
- **BATCHDATA_API_KEY must be set on the Web Service specifically** — it was missing there once even though it was in `.env` locally, causing a `RuntimeError` on every ingest call. Check Render's Environment tab on the Web Service (not just the Cron Job) if ingestion ever fails with a "not set" error.
- **Owner phone/email from skip-trace are not yet reliable** — see §6. Mailing address and owner name work; phone/email are best-effort until the real response shape is confirmed (this is now the single biggest remaining unconfirmed piece).
- **`/leads/ingest`'s `estimated_cost_usd` only covers skip-trace, NOT search** — this significantly understates real cost (confirmed: reported $0.35, actual spend $8.67 on the 2026-07-29 test). Always check the actual BatchData wallet balance for true cost, don't trust this field alone.
- **Search cost per lead is high and not yet optimized** — $8.67 for 5 leads (~$1.73/lead) on an unfiltered county-wide query. The real lever to reduce this is tighter `quicklists`/geographic targeting so fewer irrelevant (and billed) records get returned per search call — not yet tuned.
- **Two real cost incidents tonight, both fully explained and fixed**: (1) 2026-07-28, $21.90 spent believing search was free; (2) 2026-07-29, count-preview feature had a wrong field-name guess (safeguard caught it correctly, no money lost from this one specifically). Both root causes are documented in the status note at the top — read it before making further BatchData changes.

## 10. Next steps, in priority order

1. **Optimize search cost per lead** — test whether county+quicklists combinations can be narrowed further to reduce the ~$1.73/lead rate seen on the first real 5-lead pull
2. **In progress**: post-scoring quality gate in `lead_scoring.py` — leads scoring below 50 should get `status='low_priority'` instead of `'scored'`, hidden from the default dashboard view but not deleted (deferred to a later session, per plan agreed 2026-07-28 — this is "Step 2" of a two-step plan; Step 1, the ingestion-time weak-signal filter, is done)
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
