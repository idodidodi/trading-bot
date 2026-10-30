# Cloudflare diagnostics

Use the official Wrangler CLI and Cloudflare API before reading the dashboard UI.
Worker: `family-trading-bot`; account: `e1ad777d3bfb5c9d840ec7bb18616e3f`.

```sh
npx --yes wrangler@4.155.0 whoami
npx --yes wrangler@4.155.0 deployments list --name family-trading-bot --json
```

If authentication expires, run `npx --yes wrangler@4.155.0 login` and let the owner approve OAuth. Credentials stay in Wrangler's user configuration, outside the repository. Never print or commit tokens.

Historical CI build logs are available through the Cloudflare Builds API:
`GET /accounts/e1ad777d3bfb5c9d840ec7bb18616e3f/builds/builds/{build_uuid}/logs`.
Use an authenticated API client with the required Builds read permission; do not assume a Wrangler login grants that permission. See https://developers.cloudflare.com/workers/ci-cd/builds/api-reference/.

The dashboard is a fallback when CLI/API permissions are insufficient. `wrangler tail` reads runtime requests, not historical build logs.

## Preview builds

`npx wrangler preview` requires a top-level `"previews": {}` block.
The Wrangler build command runs `node cloud/build.mjs` to generate `dist/` on clean checkouts. The static builder uses an explicit public-file allowlist and does not read `.env` or the database.

Validate without deployment:

```sh
npx --yes wrangler@4.155.0 deploy --dry-run
```

After publishing a configuration fix, confirm the branch preview build succeeds and inspect its URL. Production deployments and preview builds have separate status; check the production deployment independently.
