from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from mockazure.tls import ensure_tls, renew_leaf


def test_expired_leaf_requires_renewal_without_ca_rotation(tmp_path):
    files = ensure_tls(tmp_path, ())
    original_ca = files.ca_cert.read_bytes()
    ca = x509.load_pem_x509_certificate(original_ca)
    ca_key = serialization.load_pem_private_key(files.ca_key.read_bytes(), None)
    leaf = x509.load_pem_x509_certificate(files.server_cert.read_bytes())
    now = datetime.now(timezone.utc)
    expired = (
        x509.CertificateBuilder()
        .subject_name(leaf.subject)
        .issuer_name(ca.subject)
        .public_key(leaf.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now - timedelta(days=1))
    )
    for extension in leaf.extensions:
        expired = expired.add_extension(extension.value, extension.critical)
    files.server_cert.write_bytes(
        expired.sign(ca_key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM)
    )
    with pytest.raises(ValueError, match="renew"):
        ensure_tls(tmp_path, ())
    renew_leaf(tmp_path, ())
    ensure_tls(tmp_path, ())
    assert files.ca_cert.read_bytes() == original_ca
