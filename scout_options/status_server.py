"""Local dashboard server with an exact file allowlist; ledger is never served."""
import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class StatusHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path not in {'/', '/options.html', '/options-status.json'}:
            self.send_error(404)
            return
        self.path = '/options.html' if path == '/' else path
        super().do_GET()

    def do_HEAD(self):
        if urlsplit(self.path).path not in {'/', '/options.html', '/options-status.json'}:
            self.send_error(404)
            return
        self.path = '/options.html' if urlsplit(self.path).path == '/' else urlsplit(self.path).path
        super().do_HEAD()

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        super().end_headers()

    def log_message(self, *args):
        pass


def main():
    from functools import partial
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', required=True)
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    handler = partial(StatusHandler, directory=args.directory)
    with ThreadingHTTPServer((args.bind, args.port), handler) as server:
        server.serve_forever()


if __name__ == '__main__': main()
