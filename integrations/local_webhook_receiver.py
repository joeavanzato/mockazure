"""Local HTTPS receiver for real Graph callback qualification; no outbound traffic."""

import argparse
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def serve(cert, key, output, host="127.0.0.1", port=9444, status_file=None):
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400)
                return
            if length < 0 or length > 1024 * 1024:
                self.send_error(413)
                return
            raw = self.rfile.read(length)
            tokens = parse_qs(urlsplit(self.path).query).get("validationToken", [])
            if tokens:
                if len(tokens) != 1:
                    self.send_error(400)
                    return
                body = tokens[0].encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict) or not isinstance(payload.get("value"), list):
                    raise ValueError()
            except (ValueError, UnicodeError):
                self.send_error(400)
                return
            with lock:
                with output.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(payload) + "\n")
            status = 202
            if status_file is not None:
                status = int(status_file.read_text().strip())
                if status not in {202, 503}:
                    raise ValueError("Unsupported local receiver status")
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer((host, port), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cert", required=True, type=Path)
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--host", default="127.0.0.1", choices=["127.0.0.1", "0.0.0.0", "localhost"]
    )
    parser.add_argument("--port", default=9444, type=int)
    parser.add_argument("--status-file", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    server = serve(args.cert, args.key, args.output, args.host, args.port, args.status_file)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
