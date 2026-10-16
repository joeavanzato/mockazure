import json
import os
import socket
import ssl
import subprocess
import sys
import time

import httpx

from mockazure.bootstrap import initialize_instance


def test_identity_cli_over_https_keeps_secrets_in_output_file(database, settings, tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    files = initialize_instance(
        tmp_path / "instance",
        database_url=settings.database_url.get_secret_value(),
        advertised_host="localhost",
        port=port,
    )
    environment = {k: v for k, v in os.environ.items() if not k.startswith("MOCKAZURE_")}
    with (tmp_path / "server.log").open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "mockazure", "serve", "--config", str(files.config_file)],
            stdout=log,
            stderr=log,
            env=environment,
        )
        try:
            with httpx.Client(
                verify=ssl.create_default_context(cafile=str(files.ca_cert_file)), trust_env=False
            ) as client:
                for _ in range(100):
                    if process.poll() is not None:
                        raise AssertionError("Local API failed to start")
                    try:
                        if client.get(f"https://localhost:{port}/_mock/ready").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise AssertionError("Local API not ready")

            def cli(*args):
                return subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "mockazure",
                        *map(str, args),
                        "--config",
                        str(files.config_file),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    env=environment,
                )

            tenant = cli(
                "tenant", "create", "--slug", "lab", "--display-name", "Lab", "--domain", "lab.test"
            )
            assert tenant.returncode == 0, tenant.stderr
            tenant_id = json.loads(tenant.stdout)["id"]
            user_file = tmp_path / "user.json"
            user_file.write_text(
                json.dumps(
                    {
                        "upn": "reader@lab.test",
                        "display_name": "Reader",
                        "password": "local-password-987",
                    }
                )
            )
            user = cli("identity", "create-user", tenant_id, "--input", user_file)
            assert user.returncode == 0, user.stderr
            assert "local-password-987" not in user.stdout + user.stderr
            app_file = tmp_path / "app.json"
            app_file.write_text(json.dumps({"display_name": "App", "client_mode": "confidential"}))
            app = cli("identity", "create-application", tenant_id, "--input", app_file)
            assert app.returncode == 0, app.stderr
            app_id = json.loads(app.stdout)["client_id"]
            secret_file = tmp_path / "secret.json"
            result = cli("identity", "issue-secret", tenant_id, app_id, "--output", secret_file)
            assert result.returncode == 0, result.stderr
            secret = json.loads(secret_file.read_text())
            assert len(secret["secret"]) >= 43
            assert secret["secret"] not in result.stdout + result.stderr
            previous = secret_file.read_bytes()
            assert cli(
                "identity", "issue-secret", tenant_id, app_id, "--output", secret_file
            ).returncode
            assert secret_file.read_bytes() == previous
            assert cli("identity", "revoke-credential", tenant_id, secret["id"]).returncode == 0
            listed = cli("identity", "list", tenant_id, "users")
            assert json.loads(listed.stdout)["items"][0]["upn"] == "reader@lab.test"
        finally:
            process.terminate()
            process.wait(timeout=15)
