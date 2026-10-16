import json
import os
import socket
import ssl
import subprocess
import sys
import time

import httpx

from mockazure.bootstrap import initialize_instance
from mockazure.tls import ensure_tls


def test_cli_uses_real_https_and_rejects_wrong_ca(database, settings, tmp_path, tenant_payload):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    files = initialize_instance(
        tmp_path / "instance",
        database_url=settings.database_url.get_secret_value(),
        advertised_host="localhost",
        port=port,
    )
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("MOCKAZURE_")
    }
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
                        raise AssertionError("server failed; see project-local server.log")
                    try:
                        if client.get(f"https://localhost:{port}/_mock/ready").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise AssertionError("server was not ready")
            command = [sys.executable, "-m", "mockazure", "tenant"]
            result = subprocess.run(
                [
                    *command,
                    "create",
                    "--slug",
                    "contoso",
                    "--display-name",
                    "Contoso",
                    "--domain",
                    "contoso.test",
                    "--config",
                    str(files.config_file),
                ],
                capture_output=True,
                text=True,
                env=environment,
                timeout=15,
            )
            assert result.returncode == 0, result.stderr
            tenant = json.loads(result.stdout)
            result = subprocess.run(
                [*command, "show", tenant["id"], "--config", str(files.config_file)],
                capture_output=True,
                text=True,
                env=environment,
                timeout=15,
            )
            assert result.returncode == 0 and json.loads(result.stdout)["id"] == tenant["id"]
            wrong = ensure_tls(tmp_path / "wrong", ())
            files.ca_cert_file.write_bytes(wrong.ca_cert.read_bytes())
            result = subprocess.run(
                [sys.executable, "-m", "mockazure", "health", "--config", str(files.config_file)],
                capture_output=True,
                text=True,
                env=environment,
                timeout=15,
            )
            assert result.returncode != 0
        finally:
            process.terminate()
            process.wait(timeout=15)
