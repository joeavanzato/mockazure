import ipaddress
import ssl
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes

from mockazure.tls import ensure_tls, renew_leaf


def test_certificate_identity_sans_and_renewal(tmp_path):
    files = ensure_tls(tmp_path, ("api.mockazure.test",))
    ca = x509.load_pem_x509_certificate(files.ca_cert.read_bytes())
    leaf = x509.load_pem_x509_certificate(files.server_cert.read_bytes())
    leaf.verify_directly_issued_by(ca)
    sans = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert {"localhost", "api.mockazure.test"} <= set(sans.get_values_for_type(x509.DNSName))
    assert ipaddress.ip_address("127.0.0.1") in sans.get_values_for_type(x509.IPAddress)
    assert ensure_tls(tmp_path, ("api.mockazure.test",)) == files
    renew_leaf(tmp_path, ("api.mockazure.test",))
    assert x509.load_pem_x509_certificate(files.ca_cert.read_bytes()).fingerprint(
        hashes.SHA256()
    ) == ca.fingerprint(hashes.SHA256())
    assert (
        x509.load_pem_x509_certificate(files.server_cert.read_bytes()).serial_number
        != leaf.serial_number
    )


def test_mismatched_key_requires_explicit_repair(tmp_path):
    files = ensure_tls(tmp_path / "one", ())
    other = ensure_tls(tmp_path / "two", ())
    files.server_key.write_bytes(other.server_key.read_bytes())
    with pytest.raises(ValueError, match="renew"):
        ensure_tls(tmp_path / "one", ())


def test_generated_ca_verifies_real_https(tmp_path):
    files = ensure_tls(tmp_path, ())

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"verified")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(files.server_cert, files.server_key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        trust = ssl.create_default_context(cafile=str(files.ca_cert))
        with urllib.request.urlopen(
            f"https://localhost:{server.server_port}", context=trust
        ) as response:
            assert response.read() == b"verified"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
