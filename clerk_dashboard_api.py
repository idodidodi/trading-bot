"""Opt-in loopback API for a migrated local runtime database.

This process never starts the scanner. Provision the runtime schema and restricted
API login first; it intentionally cannot use the scanner's database credentials.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID
from urllib.parse import urlsplit

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb
from clerk_auth import SessionVerifier, AuthenticationError, MembershipError


class Gateway:
    def __init__(self, dsn, issuer, origins, installation):
        info = conninfo_to_dict(dsn)
        if info.get('host') not in ('localhost', '127.0.0.1', '::1') or info.get('hostaddr') not in (None, '127.0.0.1', '::1'):
            raise ValueError('The Clerk API requires an explicitly local PostgreSQL host')
        self.dsn = dsn
        self.installation = str(UUID(installation))
        self.issuer = issuer
        self.origins = frozenset(origins)
        self.verifier = SessionVerifier(issuer, origins, self.resolve_user)

    def connect(self):
        db = psycopg.connect(self.dsn, connect_timeout=5)
        try:
            row = db.execute("SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolinherit, rolname FROM pg_roles WHERE rolname=current_user").fetchone()
            if not row or any(row[:5]) or row[5] != 'dashboard_gateway':
                raise ValueError('Use the restricted dashboard_gateway login, never the scanner or administrator')
            db.execute("SET LOCAL statement_timeout='15s'")
            return db
        except Exception:
            db.close()
            raise

    def resolve_user(self, subject):
        with self.connect() as db:
            row = db.execute('SELECT user_id FROM api_private.clerk_identities WHERE issuer=%s AND subject=%s AND enabled',
                             (self.issuer, subject)).fetchone()
            return row[0] if row else None

    def dispatch(self, authorization, origin, payload):
        identity = self.verifier.authenticate(authorization, origin)
        if not isinstance(payload, dict) or set(payload) - {'installation', 'operation', 'input'}:
            raise ValueError('Invalid request')
        if payload.get('installation') != self.installation:
            raise MembershipError('Not authorized for this installation')
        operation, inputs = payload.get('operation'), payload.get('input', {})
        if (not isinstance(operation, str) or len(operation)>4096 or not operation.startswith('/api/')
                or urlsplit(operation).fragment or not isinstance(inputs, dict)):
            raise ValueError('Invalid operation')
        # Replay execution currently requires an Edge Function. Never silently
        # enqueue a job that has no local executor after the Auth cutover.
        if urlsplit(operation).path in ('/api/backtests', '/api/csv-backtests') and inputs:
            raise ValueError('Local replay execution is not enabled yet')
        with self.connect() as db:
            # Recheck mapping in this transaction; no privilege is inferred from
            # email, token metadata, or a caller-supplied application UUID.
            row = db.execute('SELECT user_id FROM api_private.clerk_identities WHERE issuer=%s AND subject=%s AND enabled',
                             (self.issuer, identity.subject)).fetchone()
            if not row or row[0] != identity.user_id:
                raise MembershipError('Dashboard access was revoked')
            db.execute('SET LOCAL ROLE authenticated')
            claims = {'sub': str(identity.user_id), 'role': 'authenticated'}
            db.execute("SELECT set_config('request.jwt.claims',%s,true),set_config('request.jwt.claim.sub',%s,true)",
                       (json.dumps(claims), str(identity.user_id)))
            # Existing database functions remain the authority for viewer/editor/
            # owner permissions and personal Telegram preference ownership.
            return db.execute('SELECT public.dashboard_api(%s::uuid,%s,%s::jsonb)',
                              (self.installation, operation, Jsonb(inputs))).fetchone()[0]


def handler(gateway):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, code, payload):
            data = json.dumps(payload).encode()
            self.send_response(code)
            origin = self.headers.get('Origin')
            if origin in gateway.origins:
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self):
            if self.path != '/api/dashboard' or self.headers.get('Origin') not in gateway.origins:
                return self.respond(403, {'error':'Unauthorized origin'})
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
            self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Methods', 'POST')
            self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def do_POST(self):
            if self.path != '/api/dashboard':
                return self.respond(404, {'error':'Not found'})
            if self.headers.get_content_type() != 'application/json' or self.headers.get('Transfer-Encoding'):
                return self.respond(415, {'error':'Expected bounded JSON'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 1_048_576:
                    return self.respond(413, {'error':'Invalid request size'})
                self.connection.settimeout(10)
                payload = json.loads(self.rfile.read(length))
                result = gateway.dispatch(self.headers.get('Authorization'), self.headers.get('Origin'), payload)
                self.respond(200, result)
            except AuthenticationError:
                self.respond(401, {'error':'Invalid or expired session'})
            except (MembershipError, psycopg.errors.InsufficientPrivilege):
                self.respond(403, {'error':'Not authorized'})
            except (ValueError, UnicodeError):
                self.respond(400, {'error':'Invalid or unavailable operation'})
            except psycopg.errors.RaiseException as exc:
                denied = exc.diag.message_primary in ('Not authorized', 'View-only access', 'Owner only')
                self.respond(403 if denied else 400, {'error': 'Not authorized' if denied else 'Operation rejected; reload and retry'})
            except psycopg.Error:
                self.respond(503, {'error':'Dashboard database unavailable'})
            except (TimeoutError, OSError):
                self.close_connection = True

        def do_GET(self):
            self.respond(405, {'error':'Use the authenticated dashboard endpoint'})

        def log_message(self, *args):
            pass  # Never log request authorization headers or input bodies.
    return Handler


if __name__ == '__main__':
    gateway = Gateway(os.environ['DASHBOARD_DATABASE_URL'], os.environ['CLERK_ISSUER'],
                      os.environ['DASHBOARD_ALLOWED_ORIGINS'].split(','), os.environ['DASHBOARD_INSTALLATION_ID'])
    server = ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('DASHBOARD_API_PORT', '8766'))), handler(gateway))
    print('Authenticated dashboard API listening on loopback; scanner not started', flush=True)
    server.serve_forever()
