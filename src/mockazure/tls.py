"""Instance-owned CA and explicitly renewable HTTPS leaf certificates."""

import ipaddress
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from mockazure.config import validate_host
from mockazure.files import file_lock, write_private


@dataclass(frozen=True)
class CertificateFiles:
    ca_cert: Path
    ca_key: Path
    server_cert: Path
    server_key: Path


def paths(data_dir):
    return CertificateFiles(
        *(data_dir / name for name in ("ca.pem", "ca.key", "server.pem", "server.key"))
    )


def private_bytes(key):
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def names(hosts):
    result = []
    for host in dict.fromkeys(("localhost", "127.0.0.1", *hosts)):
        validate_host(host)
        try:
            result.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            result.append(x509.DNSName(host))
    return result


def key_usage(ca):
    return x509.KeyUsage(
        digital_signature=True,
        content_commitment=False,
        key_encipherment=not ca,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=ca,
        crl_sign=ca,
        encipher_only=False,
        decipher_only=False,
    )


def make_certificate(key, subject, issuer, signing_key, *, ca, hosts=()):
    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=3650 if ca else 365))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=0 if ca else None), True)
        .add_extension(key_usage(ca), True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(signing_key.public_key()), False
        )
    )
    if not ca:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(names(hosts)), False
        ).add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
    return builder.sign(signing_key, hashes.SHA256())


def load_ca(files):
    try:
        cert = x509.load_pem_x509_certificate(files.ca_cert.read_bytes())
        key = serialization.load_pem_private_key(files.ca_key.read_bytes(), password=None)
        if not (cert.public_key().public_numbers() == key.public_key().public_numbers()):
            raise ValueError("Certificate validation failed")
        cert.verify_directly_issued_by(cert)
        if not (cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca):
            raise ValueError("Certificate validation failed")
        if not (cert.not_valid_before_utc <= datetime.now(timezone.utc) < cert.not_valid_after_utc):
            raise ValueError("Certificate validation failed")
        return cert, key
    except Exception:
        raise ValueError("Invalid CA material; restore the instance CA from backup") from None


def validate_leaf(files, ca, hosts):
    try:
        leaf = x509.load_pem_x509_certificate(files.server_cert.read_bytes())
        key = serialization.load_pem_private_key(files.server_key.read_bytes(), password=None)
        leaf.verify_directly_issued_by(ca)
        if not (leaf.public_key().public_numbers() == key.public_key().public_numbers()):
            raise ValueError("Certificate validation failed")
        if not (leaf.not_valid_before_utc <= datetime.now(timezone.utc) < leaf.not_valid_after_utc):
            raise ValueError("Certificate validation failed")
        if not (not leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca):
            raise ValueError("Certificate validation failed")
        if (
            ExtendedKeyUsageOID.SERVER_AUTH
            not in leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        ):
            raise ValueError("Certificate validation failed")
        if not (
            set(names(hosts))
            <= set(leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value)
        ):
            raise ValueError("Certificate validation failed")
    except Exception:
        raise ValueError(
            "Invalid or expired server certificate; run cert renew explicitly"
        ) from None


def new_leaf(files, ca, ca_key, hosts, *, replace=False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = make_certificate(
        key,
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MockAzure local server")]),
        ca.subject,
        ca_key,
        ca=False,
        hosts=hosts,
    )
    for path, content in (
        (files.server_key, private_bytes(key)),
        (files.server_cert, cert.public_bytes(serialization.Encoding.PEM)),
    ):
        if replace:
            temporary = path.with_name(path.name + "." + uuid4().hex)
            write_private(temporary, content)
            os.replace(temporary, path)
        else:
            write_private(path, content)


def ensure_tls(data_dir: Path, hosts: tuple[str, ...]) -> CertificateFiles:
    data_dir = data_dir.resolve()
    names(hosts)
    with file_lock(data_dir, ".tls.lock"):
        files = paths(data_dir)
        present = [path.exists() for path in vars(files).values()]
        if any(present) and not all(present):
            raise ValueError("Incomplete TLS material; restore the instance files")
        if not any(present):
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MockAzure local CA")])
            ca = make_certificate(key, subject, subject, key, ca=True)
            write_private(files.ca_key, private_bytes(key))
            write_private(files.ca_cert, ca.public_bytes(serialization.Encoding.PEM))
            new_leaf(files, ca, key, hosts)
        ca, _ = load_ca(files)
        validate_leaf(files, ca, hosts)
        return files


def renew_leaf(data_dir: Path, hosts: tuple[str, ...]) -> CertificateFiles:
    names(hosts)
    with file_lock(data_dir, ".tls.lock"):
        files = paths(data_dir)
        ca, key = load_ca(files)
        new_leaf(files, ca, key, hosts, replace=True)
        validate_leaf(files, ca, hosts)
        return files


def validate_tls(data_dir: Path, hosts: tuple[str, ...]) -> CertificateFiles:
    """Validate existing credentials without writing to shared runtime mounts."""
    files = paths(data_dir.resolve())
    ca, _ = load_ca(files)
    validate_leaf(files, ca, hosts)
    return files
