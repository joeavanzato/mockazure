import asyncio
import os
import subprocess
import sys
import time

from mockazure.bootstrap import initialize_instance
from mockazure.jobs.repository import enqueue_export, get_export_result, get_job
from mockazure.tenants.repository import create_tenant
from mockazure.tenants.schemas import TenantCreate


async def test_killed_claim_is_recovered_by_two_real_workers(
    database, settings, tmp_path, tenant_payload
):
    files = initialize_instance(
        tmp_path / "instance",
        database_url=settings.database_url.get_secret_value(),
        advertised_host="localhost",
        port=8443,
    )
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("MOCKAZURE_")
    }
    async with database.sessions.begin() as session:
        tenant = await create_tenant(session, TenantCreate(**tenant_payload))
        first = await enqueue_export(session, tenant.id)
    marker = tmp_path / "claimed.txt"
    processes = []
    with (tmp_path / "workers.log").open("w") as log:
        try:
            stalled = subprocess.Popen(
                [
                    sys.executable,
                    "tests/support/claim_and_wait.py",
                    str(files.config_file),
                    str(marker),
                ],
                env=environment,
                stdout=log,
                stderr=log,
            )
            processes.append(stalled)
            for _ in range(100):
                if marker.exists():
                    break
                assert stalled.poll() is None
                await asyncio.sleep(0.1)
            assert marker.read_text() == str(first.id)
            stalled.kill()
            stalled.wait(timeout=10)
            async with database.sessions.begin() as session:
                jobs = [first] + [await enqueue_export(session, tenant.id) for _ in range(5)]
            for _ in range(2):
                processes.append(
                    subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "mockazure",
                            "worker",
                            "--config",
                            str(files.config_file),
                        ],
                        env=environment,
                        stdout=log,
                        stderr=log,
                    )
                )
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                async with database.sessions() as session:
                    statuses = [await get_job(session, tenant.id, job.id) for job in jobs]
                if all(job.state == "succeeded" for job in statuses):
                    break
                await asyncio.sleep(0.1)
            assert all(job.state == "succeeded" for job in statuses)
            assert [job.attempts for job in statuses] == [2, 1, 1, 1, 1, 1]
            async with database.sessions() as session:
                for job in jobs:
                    result = await get_export_result(session, tenant.id, job.id)
                    assert result.source_tenant_id == tenant.id
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=10)
