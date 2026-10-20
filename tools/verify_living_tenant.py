"""Qualify connected tenants through local HTTPS and original Microsoft clients."""

import argparse
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select

from mockazure.admin.client import AdminClient
from mockazure.asyncio_support import run
from mockazure.config import load_settings
from mockazure.db.engine import close_database, create_database
from mockazure.population.identity import resolve_identity
from mockazure.population.models import EntityBindingRow, ResourceBinding
from mockazure.population.transport import LocalTransport
from mockazure.population.types import AdapterContext
from tools.verify_lab_console import save, source_fingerprint, submit_preview, terminal


def validate_report(report):
    required = {"population", "graph", "storage", "file", "queue", "table", "exchange", "replay"}
    try:
        assert report["phase"] == "content" and report["passed"] is True
        assert required == set(report["cases"])
        assert all(case["state"] == "passed" for case in report["cases"].values())
        assert re.fullmatch("[0-9a-f]{64}", report["source_fingerprint"])
        cases = report["cases"]
        expected_users = 20 if report["scale"] == "small" else 2500
        assert cases["graph"]["users"] == cases["exchange"]["mailboxes"] == expected_users
        assert cases["exchange"]["commands"] == ["Get-Mailbox", "Get-EXOMailbox -Identity"]
        assert cases["storage"]["sha256"] == cases["file"]["sha256"]
        assert re.fullmatch("[0-9a-f]{64}", cases["storage"]["sha256"])
        assert cases["storage"]["bytes"] == cases["file"]["bytes"] > 0
        assert cases["replay"]["created"] == 0
    except (KeyError, TypeError, AssertionError):
        raise ValueError("Incomplete living-tenant content qualification") from None


async def qualify(config, phase, output, scale="small", distribution="native"):
    if phase != "content":
        raise ValueError("Only content qualification is implemented at this stage")
    from tools.living_tenant_clients import collect

    settings = load_settings(config)
    database = create_database(settings)
    stamp = uuid4().hex[:10]
    report = {
        "schema_version": 1,
        "phase": phase,
        "scale": scale,
        "distribution": distribution,
        "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True)),
        "source_fingerprint": source_fingerprint(),
        "passed": False,
        "started_at": datetime.now(UTC).isoformat(),
        "cases": {},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    save(output, report)
    try:
        async with AdminClient(
            str(settings.admin_base_url), ca_file=settings.ca_file, token_file=settings.token_file
        ) as admin:
            admin.http.timeout = 120
            request = {
                "bundle_id": "builtin",
                "template": "living-small-business" if scale == "small" else "living-enterprise",
                "dataset_namespace": "living-" + stamp,
                "target": {
                    "create_tenant": {
                        "slug": "living-" + stamp,
                        "display_name": "Living native qualification " + stamp,
                        "domain": "living-" + stamp + ".test",
                        "licences": ["E5"],
                    }
                },
                "parameters": {"shared.referenceTime": "2026-10-07T00:00:00Z"},
            }
            _, submitted, _ = await submit_preview(admin, request)
            result = await terminal(admin, submitted["id"])
            assert result["state"] == "succeeded", result["errors"]
            tenant_id = UUID(result["tenant_id"])
            async with database.sessions() as session:
                refs = {
                    row.key: row.actual_id
                    for row in await session.scalars(
                        select(ResourceBinding).where(
                            ResourceBinding.tenant_id == tenant_id,
                            ResourceBinding.namespace == request["dataset_namespace"],
                        )
                    )
                }
                entities = {
                    row.logical_key: row.actual_id
                    for row in await session.scalars(
                        select(EntityBindingRow).where(
                            EntityBindingRow.tenant_id == tenant_id,
                            EntityBindingRow.namespace == request["dataset_namespace"],
                        )
                    )
                }
            ctx = AdapterContext(database, settings, tenant_id)
            credential = await resolve_identity(ctx, refs["client"])
            transport = LocalTransport(ctx, credential)
            report.update(tenant_id=str(tenant_id), run_id=submitted["id"])
            report["cases"]["population"] = {"state": "passed", "progress": result["progress"]}
            report["cases"].update(
                await collect(
                    settings,
                    admin,
                    ctx,
                    credential,
                    transport,
                    refs,
                    entities,
                    request["target"]["create_tenant"]["domain"],
                    20 if scale == "small" else 2500,
                )
            )
            request["target"] = {"existing_tenant_id": str(tenant_id)}
            _, repeated, _ = await submit_preview(admin, request)
            replay = await terminal(admin, repeated["id"])
            assert replay["state"] == "succeeded" and replay["progress"]["created"] == 0
            report["cases"]["replay"] = {"state": "passed", "created": 0}
            report["passed"] = True
            report["completed_at"] = datetime.now(UTC).isoformat()
            validate_report(report)
            save(output, report)
    except Exception:
        report["passed"] = False
        report["failure"] = "Qualification incomplete; inspect the local command output."
        save(output, report)
        raise
    finally:
        await close_database(database)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--phase", choices=["content", "evidence", "identity", "hunting", "all"], required=True
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--scale", choices=["small", "large"], default="small")
    parser.add_argument("--distribution", choices=["native", "docker"], default="native")
    args = parser.parse_args()
    run(qualify(args.config, args.phase, args.report, args.scale, args.distribution))


if __name__ == "__main__":
    main()
