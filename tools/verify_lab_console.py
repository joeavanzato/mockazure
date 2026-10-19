"""Run real local HTTPS profile workflows; keep exact cases and bounded public counts."""

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import platform
import re
import subprocess
import time
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID, uuid4

from sqlalchemy import select

from mockazure.admin.client import AdminClient
from mockazure.asyncio_support import run
from mockazure.config import load_settings
from mockazure.db.engine import close_database, create_database
from mockazure.endpoints import local_origin
from mockazure.population.catalog import list_families
from mockazure.population.identity import resolve_identity
from mockazure.population.identity_models import LabIdentity
from mockazure.population.transport import LocalTransport
from mockazure.population.types import AdapterContext
from mockazure.profiles.builtin import builtin_bundle
from mockazure.profiles.resolver import resolve_template

REQUIRED_PROFILES = ("developer-lab", "collection-lab", "investigation-lab", "volume-lab")


def source_fingerprint():
    """Identical for source, wheel and sdist installs; excludes bytecode/installation metadata."""
    base = Path(str(files("mockazure")))
    digest = hashlib.sha256()
    for path in sorted(base.rglob("*"), key=lambda p: p.relative_to(base).as_posix()):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            digest.update(path.relative_to(base).as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def expected_steps(name):
    profile = resolve_template(
        builtin_bundle(),
        name,
        {
            "shared.users": 10000 if name == "volume-lab" else 20,
            "shared.referenceTime": "2026-10-07T00:00:00Z",
        },
        datetime(2026, 10, 7, tzinfo=UTC),
    )
    return {
        name + ":" + key: {"family": step.family, "count": step.parameters.get("count", 1)}
        for key, step in profile.steps.items()
    }


def validate_report(value, *, expected_fingerprint, profiles=REQUIRED_PROFILES):
    try:
        assert value["schema_version"] == 1
        assert value["source_fingerprint"] == expected_fingerprint
        assert re.fullmatch(r"[0-9a-f]{40}", value["source_sha"])
        assert value["distribution"]["kind"] and value["dependencies"]["python"]
        assert not value["failed_cases"]
        assert set(value["profiles"]) == set(profiles)
        catalog = {family.key: family for family in list_families()}
        assert set(value["catalog"]) == set(catalog)
        for key, result in value["catalog"].items():
            assert result["browse_case"] == "browse:" + key and result["state"] == "passed"
            assert result["operations"] == [op.key for op in catalog[key].operations]
        for name in profiles:
            result = value["profiles"][name]
            assert result["state"] == result["replay_state"] == "succeeded"
            assert result["replay_created"] == 0 and result["run_id"]
            assert re.fullmatch(r"[0-9a-f]{64}", result["digest"])
            assert result["seconds"] >= 0 and result["steps"]
            expected = expected_steps(name)
            assert {step["case_id"] for step in result["steps"]} == set(expected)
            cases = set()
            for step in result["steps"]:
                assert step["case_id"] not in cases and step["case_id"].startswith(name + ":")
                cases.add(step["case_id"])
                assert {"family": step["family"], "count": step["count"]} == expected[
                    step["case_id"]
                ]
                assert step["family"] in catalog and step["state"] == "succeeded"
                assert step["offset"] == step["count"] and step["count"] > 0
            assert result["public_counts"]["users"] == (10000 if name == "volume-lab" else 20)
            expected_signins = (
                100000 if name == "volume-lab" else (120 if name == "investigation-lab" else 100)
            )
            assert result["public_counts"]["signins"] == expected_signins
            if name == "volume-lab":
                assert result["interrupted_resumed"] is True
        assert value["unsupported_case"] == {"case_id": "unsupported-family", "status": 404}
    except (AssertionError, KeyError, TypeError, ValueError):
        raise ValueError("Incomplete or inconsistent lab qualification report") from None


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


async def terminal(client, identifier, *, timeout=7200):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = await client.request("GET", f"/_mock/population/runs/{identifier}")
        if value["state"] not in {"pending", "running", "verifying"}:
            return value
        await asyncio.sleep(1)
    raise RuntimeError("Run did not finish before qualification deadline")


async def public_counts(database, settings, tenant):
    async with database.sessions() as session:
        binding = await session.scalar(select(LabIdentity).where(LabIdentity.tenant_id == tenant))
    if binding is None:
        raise RuntimeError("Starter client identity is missing")
    ctx = AdapterContext(database, settings, tenant)
    transport = LocalTransport(ctx, await resolve_identity(ctx, binding.name))
    result = {}
    for key, path in (("users", "/v1.0/users"), ("signins", "/v1.0/auditLogs/signIns")):
        count, pages, params = 0, 0, {"$top": "999"}
        seen = set()
        while True:
            response = await transport.request("graph", "GET", path, params=params)
            if response.status_code == 429:
                await asyncio.sleep(min(60, max(1, int(response.headers.get("retry-after", 1)))))
                continue
            if response.status_code != 200:
                raise RuntimeError(f"Public {key} query returned HTTP {response.status_code}")
            page = response.json()
            count += len(page["value"])
            pages += 1
            link = page.get("@odata.nextLink")
            if not link:
                break
            parsed = urlsplit(link)
            endpoints = settings.population_endpoints or settings.endpoints
            if parsed.netloc != urlsplit(endpoints.graph).netloc or link in seen or pages > 10000:
                raise RuntimeError("Invalid local Graph continuation")
            seen.add(link)
            path, params = parsed.path, dict(parse_qsl(parsed.query))
        result[key], result[key + "_pages"] = count, pages
    return result


async def qualify(config, profiles, output, *, resume=False, recheck=False, distribution="native"):
    settings = load_settings(config)
    local_origin(str(settings.admin_base_url))
    fingerprint = source_fingerprint()
    if output.exists() and not (resume or recheck):
        raise ValueError("Report exists; use --resume or a new output path")
    if resume or recheck:
        report = json.loads(output.read_text())
        if recheck:
            report.setdefault("prior_provenance", []).append(
                {
                    "source_sha": report["source_sha"],
                    "source_fingerprint": report["source_fingerprint"],
                    "distribution": report["distribution"],
                    "completed_at": report.get("completed_at"),
                }
            )
            report["qualification_mode"] = (
                "rechecked existing dataset using fresh public reads and apply"
            )
            report["source_fingerprint"] = fingerprint
            report["source_sha"] = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip()
            report["distribution"]["kind"] = distribution
            for result in report["profiles"].values():
                result.pop("replay_state", None)
        if report["source_fingerprint"] != fingerprint:
            raise ValueError("Source changed since the saved qualification run")
    else:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        report = {
            "schema_version": 1,
            "started_at": datetime.now(UTC).isoformat(),
            "source_sha": sha,
            "source_fingerprint": fingerprint,
            "distribution": {
                "kind": distribution,
                "mockazure_version": importlib.metadata.version("mockazure"),
            },
            "dependencies": {
                "python": platform.python_version(),
                **{
                    name: importlib.metadata.version(name)
                    for name in ("fastapi", "sqlalchemy", "psycopg", "cryptography", "pyyaml")
                },
            },
            "profiles": {},
            "catalog": {},
            "failed_cases": [],
            "limitations": [
                "Catalog browsing includes empty families; it is not an all-operations claim.",
                "Fixture records use administration; "
                "service actions use ordinary local permissions.",
                "No guest compute, real mail delivery, proprietary detection, or SMB/NFS.",
                "Volume proves bounded processing and exact counts, not a performance SLA.",
            ],
        }
    report["failed_cases"] = []
    database = create_database(settings)
    try:
        async with AdminClient(
            str(settings.admin_base_url), ca_file=settings.ca_file, token_file=settings.token_file
        ) as client:
            client.http.timeout = 120
            for name in profiles:
                result = report["profiles"].setdefault(name, {})
                if result.get("replay_state") == "succeeded":
                    continue
                started = time.monotonic()
                if "run_id" not in result:
                    label = "qual-" + name.removesuffix("-lab") + "-" + uuid4().hex[:8]
                    request = {
                        "bundle_id": "builtin",
                        "template": name,
                        "dataset_namespace": label,
                        "parameters": {
                            "shared.users": 10000 if name == "volume-lab" else 20,
                            "shared.referenceTime": "2026-10-07T00:00:00Z",
                        },
                        "target": {
                            "create_tenant": {
                                "slug": label,
                                "display_name": label,
                                "domain": label + ".test",
                                "licences": ["E5"],
                            }
                        },
                    }
                    preview = await client.request(
                        "POST", "/_mock/population/previews", json=request
                    )
                    created = await client.request(
                        "POST",
                        "/_mock/population/runs",
                        json={"preview_id": preview["id"], "digest": preview["digest"]},
                    )
                    result.update(
                        run_id=created["id"],
                        tenant_id=created["tenant_id"],
                        digest=preview["resolved_digest"],
                        request=request,
                        interrupted_resumed=False,
                    )
                    save(output, report)
                if name == "volume-lab" and not result["interrupted_resumed"]:
                    for _ in range(300):
                        value = await client.request(
                            "GET", f"/_mock/population/runs/{result['run_id']}"
                        )
                        if value["progress"].get("created", 0) >= 100:
                            break
                        if value["state"] not in {"pending", "running", "verifying"}:
                            raise RuntimeError(
                                "Volume run ended before the interruption checkpoint"
                            )
                        await asyncio.sleep(1)
                    await client.request(
                        "POST", f"/_mock/population/runs/{result['run_id']}/cancel"
                    )
                    stopped = await terminal(client, result["run_id"])
                    assert stopped["state"] == "cancelled"
                    result["checkpoint_progress"] = stopped["progress"]
                    await client.request(
                        "POST", f"/_mock/population/runs/{result['run_id']}/resume"
                    )
                    result["interrupted_resumed"] = True
                    save(output, report)
                final = await terminal(client, result["run_id"])
                if final["state"] != "succeeded":
                    result.update(state=final["state"], errors=final["errors"])
                    save(output, report)
                    raise RuntimeError(f"{name} population {final['state']}")
                result.update(state=final["state"], progress=final["progress"])
                steps = (
                    await client.request(
                        "GET", f"/_mock/population/runs/{result['run_id']}/steps?limit=500"
                    )
                )["items"]
                result["steps"] = [{**step, "case_id": name + ":" + step["key"]} for step in steps]
                result["public_counts"] = await public_counts(
                    database, settings, UUID(result["tenant_id"])
                )
                request = {
                    **result["request"],
                    "target": {"existing_tenant_id": result["tenant_id"]},
                }
                preview = await client.request("POST", "/_mock/population/previews", json=request)
                repeated = await client.request(
                    "POST",
                    "/_mock/population/runs",
                    json={"preview_id": preview["id"], "digest": preview["digest"]},
                )
                repeated = await terminal(client, repeated["id"])
                result.update(
                    replay_state=repeated["state"],
                    replay_created=repeated["progress"].get("created"),
                    replay_run_id=repeated["id"],
                    seconds=round(time.monotonic() - started, 3),
                )
                save(output, report)
                print(
                    f"{name}: {result['state']}; public counts {result['public_counts']}; "
                    f"replay created {result['replay_created']}",
                    flush=True,
                )
            tenant = report["profiles"][profiles[0]]["tenant_id"]
            for family in list_families():
                page = await client.request(
                    "GET", f"/_mock/tenants/{tenant}/resources/{family.key}?limit=1"
                )
                assert len(page["items"]) <= 1
                report["catalog"][family.key] = {
                    "browse_case": "browse:" + family.key,
                    "state": "passed",
                    "returned": len(page["items"]),
                    "operations": [op.key for op in family.operations],
                    "applied_cases": [
                        step["case_id"]
                        for value in report["profiles"].values()
                        for step in value["steps"]
                        if step["family"] == family.key
                    ],
                }
            unsupported = await client.http.get(f"/_mock/tenants/{tenant}/resources/not.a.family")
            assert unsupported.json()["error"]["message"]
            report["unsupported_case"] = {
                "case_id": "unsupported-family",
                "status": unsupported.status_code,
            }
            report["completed_at"] = datetime.now(UTC).isoformat()
            validate_report(report, expected_fingerprint=fingerprint, profiles=profiles)
            save(output, report)
    except Exception:
        report["failed_cases"] = ["qualification-incomplete"]
        save(output, report)
        raise
    finally:
        await close_database(database)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--profiles", nargs="+", choices=REQUIRED_PROFILES, default=list(REQUIRED_PROFILES)
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--recheck",
        action="store_true",
        help="Recheck saved datasets on the current code; preserve prior provenance",
    )
    parser.add_argument("--distribution", default="native")
    args = parser.parse_args()
    run(
        qualify(
            args.config,
            args.profiles,
            args.report,
            resume=args.resume,
            recheck=args.recheck,
            distribution=args.distribution,
        )
    )


if __name__ == "__main__":
    main()
