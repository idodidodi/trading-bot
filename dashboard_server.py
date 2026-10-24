"""Loopback static dashboard. All browser data access goes through Supabase Auth/RPC."""
import json
import os
import sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer


def browser_config(root):
    path=Path(os.environ.get('DASHBOARD_CONFIG_FILE',str(Path(root)/'cloud/browser-config.json')))
    if not path.is_file() and getattr(sys,'frozen',False):
        path=Path(sys._MEIPASS)/'cloud/browser-config.json'
    value=json.loads(path.read_text()) if path.is_file() else {}
    keys={'supabaseUrl':'DASHBOARD_SUPABASE_URL','publishableKey':'DASHBOARD_PUBLISHABLE_KEY','installationId':'DASHBOARD_INSTALLATION_ID'}
    result={k:(os.environ.get(env) or value.get(k,'')) for k,env in keys.items()}
    if not result['supabaseUrl'].startswith('https://') or not all(result.values()):
        raise ValueError('Configure public DASHBOARD_SUPABASE_URL, DASHBOARD_PUBLISHABLE_KEY and DASHBOARD_INSTALLATION_ID')
    # Never serialize an env mapping or a service/worker credential.
    return result


def handler(root):
    root=Path(root);config=browser_config(root)
    files={'app.js':'text/javascript','style.css':'text/css','chart.js':'text/javascript','vendor/lightweight-charts.js':'text/javascript','config.js':'text/javascript','version.json':'application/json','favicon.svg':'image/svg+xml'}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path=self.path.split('?',1)[0]
            if path=='/web/config.js':
                data=('window.DASHBOARD_CONFIG = '+json.dumps(config)+';').encode();mime='text/javascript'
            elif path.startswith('/web/') and path[5:] in files:
                target=root/'web'/path[5:]
                if not target.is_file():self.send_error(404);return
                data=target.read_bytes();mime=files[path[5:]]
            elif not path.startswith(('/api','/web/')):
                data=(root/'web/index.html').read_bytes();mime='text/html'
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
        def do_POST(self):self.send_error(403,'Use authenticated Supabase APIs')
        def log_message(self,*args):pass
    return Handler


def serve(root,port=8080):
    server=ThreadingHTTPServer(('127.0.0.1',port),handler(root))
    print(f'Authenticated dashboard: http://127.0.0.1:{server.server_port} (no scanner or DB credentials required)',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':serve(Path(__file__).resolve().parent,int(os.environ.get('PORT','8080')))
