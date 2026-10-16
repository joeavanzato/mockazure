"""The mock's TLS: a lab CA and a server certificate for every alias (azure-identity and MSAL refuse an http
authority). Extensions satisfy Python 3.13's strict X.509 verification (CA: basic constraints + key usage + SKI;
leaf: AKI, SAN, serverAuth)."""
import datetime as dt
import ipaddress
import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

HOSTS = ("login.mockcloud", "graph.mockcloud", "outlook.mockcloud", "management.mockcloud", "mockcloud", "localhost")


def _usage(**on) -> x509.KeyUsage:
    names = ("digital_signature", "content_commitment", "key_encipherment", "data_encipherment", "key_agreement",
             "key_cert_sign", "crl_sign", "encipher_only", "decipher_only")
    return x509.KeyUsage(**{n: on.get(n, False) for n in names})


def ensure(out_dir: str, hosts=HOSTS) -> dict[str, str]:
    """ca.pem, server.pem and server.key in out_dir, generated once and reused (delete the folder to renew)."""
    paths = {"ca": os.path.join(out_dir, "ca.pem"), "cert": os.path.join(out_dir, "server.pem"),
             "key": os.path.join(out_dir, "server.key")}
    if all(os.path.exists(p) for p in paths.values()):
        return paths
    os.makedirs(out_dir, exist_ok=True)
    now = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "VTW lab mockcloud CA")])
    ca_ski = x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key())
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now)
          .not_valid_after(now + dt.timedelta(days=3650))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(_usage(digital_signature=True, key_cert_sign=True, crl_sign=True), critical=True)
          .add_extension(ca_ski, critical=False)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    san = [x509.DNSName(h) for h in hosts] + [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now).not_valid_after(now + dt.timedelta(days=397))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(_usage(digital_signature=True), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_ski), critical=False)
            .sign(ca_key, hashes.SHA256()))
    with open(paths["key"], "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    with open(paths["cert"], "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(paths["ca"], "wb") as f:               # last: its presence means the set is complete
        f.write(ca.public_bytes(serialization.Encoding.PEM))
    return paths
