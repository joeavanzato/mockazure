"""python -m mockcloud: the lab's mock Microsoft cloud (lab/compose.lab.yml service `mockcloud`)."""
import os

from mockcloud.certs import HOSTS, ensure
from mockcloud.server import Mock, serve
from tools.synth import cloud


def main() -> None:
    cert_dir = os.environ.get("MOCKCLOUD_CERT_DIR", "/out")
    ensure(cert_dir, HOSTS)
    users = os.environ.get("MOCKCLOUD_USERS")
    mock = Mock(seed=int(os.environ.get("MOCKCLOUD_SEED", cloud.SEED)), users=int(users) if users else None)
    port = int(os.environ.get("MOCKCLOUD_PORT", "8443"))
    srv = serve(mock, "0.0.0.0", port, cert_dir, quiet=os.environ.get("MOCKCLOUD_QUIET") == "1")
    print(f"mockcloud on https://0.0.0.0:{port}: " + ", ".join(
        f"{t.spec.domain} ({t.n_users} users)" for t in mock.tenants.values()), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
