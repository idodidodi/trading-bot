// Dependency-free static UI build. Never read the local .env or database.
import {readFile, mkdir, rm, copyFile, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve, dirname} from 'node:path';
import {buildManifest} from './version.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const defaults = JSON.parse(await readFile(resolve(root, 'cloud/browser-config.json'), 'utf8'));
const clerkMode = process.env.DASHBOARD_AUTH_PROVIDER === 'clerk';
const config = clerkMode ? {
  authProvider: 'clerk',
  clerkPublishableKey: process.env.DASHBOARD_CLERK_PUBLISHABLE_KEY,
  apiOrigin: process.env.DASHBOARD_API_ORIGIN,
  installationId: process.env.DASHBOARD_INSTALLATION_ID || defaults.installationId,
} : {
  supabaseUrl: process.env.DASHBOARD_SUPABASE_URL || defaults.supabaseUrl,
  publishableKey: process.env.DASHBOARD_PUBLISHABLE_KEY || defaults.publishableKey,
  installationId: process.env.DASHBOARD_INSTALLATION_ID || defaults.installationId,
};
const url = new URL(clerkMode ? config.apiOrigin : config.supabaseUrl);
if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || url.pathname !== '/') {
  throw new Error('Dashboard Supabase URL must be an HTTPS origin');
}
if (!clerkMode && !/^sb_publishable_[A-Za-z0-9_-]+$/.test(config.publishableKey)) {
  throw new Error('Only a Supabase publishable key may be included in browser assets');
}
if(clerkMode && !/^pk_live_[A-Za-z0-9=]+$/.test(config.clerkPublishableKey || ''))throw new Error('Production Clerk build requires a live publishable key');
if (!/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/i.test(config.installationId)) {
  throw new Error('Invalid dashboard installation UUID');
}

const out = resolve(root, 'dist');
await rm(out, {recursive: true, force: true});
await mkdir(resolve(out, 'web/vendor'), {recursive: true});
// Explicit list prevents private files, test files, and Python code being published.
const assets = ['index.html', 'style.css', 'app.js', 'clerk-session.js', 'chart.js', 'vendor/lightweight-charts.js', 'favicon.svg'];
for (const asset of assets) await copyFile(resolve(root, 'web', asset), resolve(out, 'web', asset));
await copyFile(resolve(root, 'web/index.html'), resolve(out, 'index.html'));
await writeFile(resolve(out, 'web/config.js'), 'window.DASHBOARD_CONFIG = ' + JSON.stringify(config) + ';\n');
await writeFile(resolve(out, 'web/version.json'), JSON.stringify(await buildManifest(root)));
console.log(`Built static dashboard in dist/ (${assets.length + 2} public files).`);
