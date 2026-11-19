// Approved Pages target; does not alter the existing Worker build defaults.
import {readFile,writeFile} from 'node:fs/promises';
const config=JSON.parse(await readFile(new URL('./clerk-browser-config.json',import.meta.url),'utf8'));
process.env.DASHBOARD_AUTH_PROVIDER='clerk';
process.env.DASHBOARD_CLERK_PUBLISHABLE_KEY=config.clerkPublishableKey;
process.env.DASHBOARD_API_ORIGIN=config.apiOrigin;
process.env.DASHBOARD_INSTALLATION_ID=config.installationId;
await import('./build.mjs');
await writeFile(new URL('../dist/_headers',import.meta.url),'/web/config.js\n  Cache-Control: no-store\n/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: strict-origin-when-cross-origin\n');
// Preview aliases do not qualify as Clerk production origins. Publish/test the
// configured custom domain only; keep the existing Worker available for rollback.
