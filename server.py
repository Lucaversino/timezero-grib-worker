"""Render entry point for the V280 ecCodes GRIB worker."""
import json
import os
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit
from grib_generator import handler as OriginalHandler, ec

# Keep a single costly generation per container; do not amplify API throttling.
_GENERATION_SLOT = threading.BoundedSemaphore(1)

class Handler(OriginalHandler):
    def do_GET(self):
        path = urlsplit(self.path).path.rstrip('/')
        if path in ('/health', '/healthz'):
            payload = json.dumps({'status': 'ok' if ec is not None else 'unavailable',
                                  'service': 'timezero-grib-worker',
                                  'eccodes': ec is not None}).encode()
            self.send_response(200 if ec is not None else 503)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if path not in ('/api/timezero-grib', '/api/timezero-grib/'):
            return self._error(404, 'Rota não encontrada')
        if not _GENERATION_SLOT.acquire(blocking=False):
            return self._error(503, 'Gerador ocupado. Tente novamente após a consulta atual.')
        try:
            return super().do_GET()
        finally:
            _GENERATION_SLOT.release()

    def log_message(self, fmt, *args):
        # Never log requests with sensitive coordinates or authorization headers.
        print('HTTP request processed', flush=True)


def main():
    if not os.getenv('TIMEZERO_WORKER_SECRET') or len(os.environ['TIMEZERO_WORKER_SECRET']) < 32:
        raise RuntimeError('Configure TIMEZERO_WORKER_SECRET com pelo menos 32 caracteres.')
    if ec is None:
        raise RuntimeError('ecCodes indisponível. Verifique dependências instaladas.')
    port = int(os.getenv('PORT', '10000'))
    print(f'TimeZero GRIB worker listening on 0.0.0.0:{port}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever(poll_interval=0.5)


if __name__ == '__main__':
    main()
