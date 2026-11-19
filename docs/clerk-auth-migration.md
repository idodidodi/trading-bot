# Clerk authentication migration plan

Prepared 19 November 2026 on `codex/clerk-auth`, based on `20e7444`.
Status: local implementation approved on 19 November. Clerk application and free
DNS zone created; production Clerk instance and its authentication DNS records
separately authorized. Application code remains local, the three existing users have been imported into Clerk after explicit approval, and the live dashboard/scanner still use their existing
configuration.

## Provisioned resources

- Clerk application: `app_3MJeD1V1plCECh00rTYUMlpY1a2` (Family Trading Bot).
- Development instance: `ins_3MJeD3EY740e8wT116sprsePmiE`.
- Production instance: `ins_3MJfKXI9yQ00ZVzkUxvdyhRjQFH`.
- Free hostname: `family-trading-bot.dynv6.net`, dynv6 zone `6235989`.
- Five Clerk CNAME records saved: `clerk`, `accounts`, `clkmail`,
  `clk._domainkey`, `clk2._domainkey`. DNS, mail and TLS certificate verified by Clerk on 19 November.
- The CLI is linked and development configuration is in ignored `.env.local`
  with mode 0600. Never include it in commits or publication artifacts.
- No dashboard/API origin address has been published in the new zone.

## Implemented locally

`web/clerk-session.js` uses the official ClerkJS 6 and Clerk UI 1 CDN bundles,
mounts sign-in/account controls, retrieves fresh session tokens and rejects
responses from an earlier session. `web/app.js` opts into this only when
`authProvider` is `clerk`; current Supabase builds retain their configuration.
The static production build rejects Clerk development keys and non-HTTPS API
origins. `dashboard_server.py` supports a loopback Clerk development preview.

`clerk_auth.py` validates RS256 signatures against the configured issuer's JWKS,
required session claims and authorized browser origin. An administrator-managed
mapping resolves the verified subject to the original application UUID. A valid
Clerk account without that mapping has no dashboard access.

`clerk_dashboard_api.py` is a separate, opt-in loopback process. It accepts only
an explicitly local database and the restricted `dashboard_gateway` login,
rechecks the mapping, sets transaction-local authenticated role/UUID claims and
calls the existing `public.dashboard_api` authorization functions. It never
starts the scanner. `cloud/local-clerk-api.sql` provisions only the mapping table
and restricted role after the full runtime/auth-UUID schema migration. It has
not been applied on Linux or Supabase. The gateway cannot edit mappings.

## Remaining cutover prerequisites (not deployment-ready)

1. Complete Clerk TLS and live origin verification, establish authenticated
   HTTPS reachability to Linux, and publish the dashboard origin. DNS registration
   alone does not expose the Linux server or host the app.
2. The three-user import is complete and checked. Validate actual password sign-in
   and populate the administrator-controlled UUID mapping in the new database.
3. Migrate the complete runtime database and permission functions to local
   PostgreSQL, including the `auth.uid()` transaction-claim contract. The existing
   local working cache alone cannot serve the new API.
4. Replace the Edge Function replay execution path. The new gateway explicitly
   rejects replay mutations until the local executor is implemented; silently
   queuing Edge-dependent work would leave jobs stuck. This is a cutover blocker.
5. Verify first-user sign-in, permission parity, account recovery and all dashboard
   operations against the new database, then request the required separate
   server/UI publication approval. Retain rollback and watch normal scanner cycles.


## Verified starting point

The UI is dependency-free JavaScript, hosted as static Cloudflare Workers assets.
It currently signs in through Supabase, keeps Supabase session tokens in session
storage and calls hosted dashboard RPCs and backtest Edge Functions. The Python
scanner's production local dashboard routes deliberately reject API access.

A read-only aggregate of Supabase Auth found three registered users, all with
verified emails and bcrypt passwords, no phone identities and only the email
provider. Two monthly active users is not the total account count. No emails,
password hashes or user identifiers were exported during this inspection.

Dashboard ownership, sharing permissions and personal Telegram preferences refer
to Supabase UUIDs; replacing these with Clerk `user_...` IDs would break ownership.

## Proposed implementation

1. Create a new Clerk application, as selected by the owner. Keep email/password
   and recovery available. Configure access for the existing family users; a new
   Clerk account must not automatically receive application membership.
2. Use Clerk's JavaScript SDK and prebuilt sign-in/account controls in the existing
   UI. Keep keys out of source except the intended public publishable key. Use
   Clerk session tokens rather than persisting Supabase refresh tokens in Clerk
   mode. On sign-out/account switch, clear private application state and reject
   late responses from the previous session.
3. Add a production authenticated Linux API. Verify token signature, configured
   issuer, expiry and authorized browser origin; resolve the verified Clerk
   subject through an administrator-controlled mapping to the existing app UUID.
   Enforce existing installation membership, viewer/editor/owner permissions and
   ownership of personal settings on every endpoint. No automatic email matching,
   client-supplied user UUIDs or user-editable metadata may grant access. Do not
   expose the current unprotected development API by removing its guards.
4. Prepare the identity migration with the official Clerk migration CLI. Preserve
   each Supabase UUID as the Clerk external ID and in the explicit backend mapping.
   Preserve verified-email status only from the source's confirmation fields.
   Check bcrypt compatibility in a dry run before promising password continuity;
   show any rejected users or required password resets. There are no current OAuth
   identities to transfer. Do not import users or send invitations until the target
   application/instance and dry-run results have been reviewed and approved.
5. Existing Supabase sessions do not become Clerk sessions; users sign in again at
   cutover. Test identity mapping, permission parity and recovery before switching
   the production UI. Keep rollback artifacts and the original provider intact
   until the replacement is verified. Do not delete the Supabase project as part
   of authentication setup.

## Relationship to the Supabase exit

Clerk integration alone does not move application tables, SQL RPCs, scanner state,
commands, delivery deduplication or backtests. The full exit also needs the local
runtime migration described in `local-postgres-working-store.md`, with one
writer, restored backups, consistent snapshots and local command processing.
Do not label an authentication-only rollout as independence from Supabase.
The verified restriction deadline is 21 November; moving Auth alone cannot protect
runtime operations still using the restricted database.

## Domain and account prerequisites

The owner selected dynv6 after declining EU.org and explicitly accepting the free
subdomain route. dynv6 does not offer an SLA and advises against critical-service
use. The owner separately approved creating the Clerk production instance and
adding its authentication DNS records, without app deployment or user import.

The JavaScript/Python application does not qualify for Clerk's documented
accountless framework list. Use the official CLI detection/quickstart fallback,
and user-completed Clerk login when required; never read existing environment
files, claim files, or ask for a secret key in chat. Do not scaffold a replacement
Next.js application just to obtain temporary keys.

## Verification and publication gates

Use only a disposable database and non-production identities. Test valid and
invalid signatures, issuer/origin mismatch, expiry, key rotation, missing mapping,
cross-user settings, all role restrictions, session switching, failure states and
absence of credentials in built assets. Start only a dashboard/API test process,
never another scanner against production; send no test alerts/orders.

Clerk's required migration-plan approval was received on 19 November. The migration skill separately requires a real-instance
dry run and fresh consent before importing users. AGENTS.md requires completed
local verification and Yes/No/Q confirmation before pushing or deploying the
finished implementation to Cloudflare and the existing Linux installation.

References:
- https://clerk.com/SKILL.md
- https://clerk.com/.well-known/skills/clerk-migrate/SKILL.md
- https://clerk.com/docs/js-frontend/getting-started/quickstart
- https://clerk.com/docs/guides/development/deployment/production
- https://nic.eu.org/register.html
- https://nic.eu.org/arf/en/contact/create/

## Local verification (19 November)

- Full backend suite: 666 tests, passed, 9 skipped, on the isolated loopback
  `trading_bot_test` cluster. An initial run lacked the PDF test dependency;
  after installing it in the disposable environment the full rerun passed.
- Browser unit suite: 49 passed, including session-switch and sign-out races.
- Static build and diff checks passed. Browser inspection confirmed the real
  Clerk development sign-in widget renders in the existing dashboard.
- Clerk doctor confirmed the application link, host login and development keys.
- Official migration dry run against production instance
  `ins_3MJfKXI9yQ00ZVzkUxvdyhRjQFH`: 3 total, 3 importable, 0 rejected,
  0 skipped, 0 warnings, password required. All have confirmed email and bcrypt;
  provider metadata contains email only. Live import subsequently completed after explicit approval (see below).
- Sensitive preparation artifacts: `.clerk/migrate-preparation/supabase-users.json`
  (contains emails, IDs, verification fields, auth metadata and password hashes),
  `/private/tmp/clerk-dry-run-private.json` and corresponding `.stderr`.
  These are private local artifacts, not publication files. The source export
  and output files are mode 0600; the preparation directory is mode 0700.
- Live sign-in, password continuity, Linux API deployment and complete runtime
  migration have not been verified. Unit/dry-run success does not prove cutover.

## Approved identity import result

Run `20261119-094254-4b8f` completed on 19 November 2026 in production instance
`ins_3MJfKXI9yQ00ZVzkUxvdyhRjQFH`: 3 created, 0 failed, 0 skipped,
0 unconfirmed, 0 not sent, 0 pending. No invitations or app cutover were performed.
The run report has no imported-without fields or errors. All three accounts were
looked up individually by original external ID: IDs matched, passwords were
enabled, emails matched and were verified, and unsafe metadata matched. This
verifies stored migration state, not a successful user password sign-in.

The official import artifacts live under
`.clerk/migrate/20261119-094254-4b8f/`; private command reports are
`/private/tmp/clerk-import-private.json` and `.stderr`. Treat all migration
artifacts as user data. The import can be reversed by the official
`clerk migrate undo 20261119-094254-4b8f` command with this app/production instance,
but reversal is destructive and requires fresh approval. Supabase accounts remain
intact. No identity mapping has been installed in the Linux runtime database.

## Hosting constraint discovered during preparation

The free dynv6 hostname works for Clerk: DNS, email and TLS all verified.
Cloudflare Workers Custom Domains require a Cloudflare-owned zone; the current
external dynv6 DNS zone is not one. Tailscale Funnel is installed on Linux but
has no serve configuration, and only supports its own tailnet DNS names.
Do not point the custom domain at a Funnel hostname and assume TLS will work.
The authenticated API can use a separate HTTPS Funnel origin if approved; the
static UI still needs hosting that accepts external DNS, or a different DNS
zone arrangement. No ingress or public API has been configured.

Free-hosting adjustment approved by the owner on 19 November: Cloudflare Pages at
`app.family-trading-bot.dynv6.net` (external CNAME supported), plus a distinct
Tailscale Funnel HTTPS API origin with mandatory Clerk tokens. This changes the
previous Worker/Tunnel hosting plan, but the normal separate server/UI publication approvals are still required.

## Prepared hosting files

- `cloud/pages-build.mjs` builds the selected Clerk-only static bundle using
  `cloud/clerk-browser-config.json`; the existing Worker build remains unchanged
  unless Clerk mode is explicitly selected.
- The live public key is constructed from the verified Frontend API hostname
  using Clerk's `buildPublishableKey` algorithm:
  https://github.com/clerk/javascript/blob/main/packages/shared/src/keys.ts .
  No production secret key was read or added to the UI/API configuration.
- `deploy/trading-bot-api.service` is a separate loopback API service, not a
  scanner. Its existing Linux Python path was checked. The private environment
  file must be provisioned from `deploy/dashboard-api.env.example` after the
  local runtime database exists.
- The approved browser origin is `https://app.family-trading-bot.dynv6.net`;
  the proposed API origin is `https://ido-portege-r700.tail091280.ts.net`.
  Tailscale HTTPS/Funnel is not enabled yet. PostgreSQL remains private.
- Before any publication, create the Pages project and bind the custom hostname
  through Cloudflare, then use the exact DNS target returned by Cloudflare.
  Merely writing a CNAME is not sufficient. After local API denial tests pass,
  Funnel may point only to the dedicated API loopback port 8766, never to the
  scanner's development/admin server or PostgreSQL. All publication still needs
  the repository's separate confirmations and exact-SHA checks.
- Clerk production JWKS endpoint returned HTTPS 200 with one signing key using
  certifi certificate verification. The verifier now explicitly uses that trusted
  CA bundle; a system-Python trust-store failure was not bypassed.
- Cloudflare CLI access was unavailable during preparation (logged out); owner
  login was requested. No Pages project, custom dashboard record, Funnel exposure,
  production API process or live-code deployment has been created by this change.
