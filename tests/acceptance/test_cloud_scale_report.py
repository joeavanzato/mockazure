"""Scale acceptance cannot be satisfied by smaller or incomplete runs."""

import copy
import json
from pathlib import Path

import pytest


def valid_report():
    report = {
        "sourceCommit": "a" * 40,
        "sourceFingerprint": "b" * 64,
        "passed": True,
        "records": 100000,
        "binaryBytes": 268435456,
        "apiReplicas": 2,
        "datasets": {
            "inventory": {"expected": 100, "actual": 100},
            "diagnostic": {"expected": 1000, "actual": 1000},
            "table": {"expected": 98900, "actual": 98900},
        },
        "readers": [{"id": i, "exactIds": True, "crossTenantSentinelHits": 0} for i in range(8)],
        "restarts": [{"completed": True}, {"completed": True}],
        "binary": {"uploadedSha256": "c" * 64, "downloadedSha256": "c" * 64, "bytes": 268435456},
        "lostAcknowledgedWrites": 0,
        "acknowledgedWrites": 32,
        "tenantIsolation": True,
        "queueContention": True,
        "fileRanges": True,
        "diagnosticFanout": True,
        "queryJoins": True,
        "requests": {
            "scope": "workload-http-transports",
            "attempts": 3,
            "responses": 3,
            "transportFailures": 0,
            "httpErrorResponses": 1,
            "serverErrorResponses": 0,
            "statusCounts": {"200": 2, "403": 1},
            "channelAttempts": {"rest": 2, "sdk": 1},
        },
        "observations": {
            "databaseBytes": 1,
            "samples": [{"memory": 1}],
            "processHighWaterKiB": [1],
        },
    }
    for reader in report["readers"]:
        for name, dataset in report["datasets"].items():
            reader[name + "Count"] = dataset["expected"]
            reader[name + "IdsSha256"] = "d" * 64
            dataset["expectedIdsSha256"] = "d" * 64
    stages = [
        "start",
        "beforeUpload",
        "afterUpload",
        "replicated",
        "beforeRestart1",
        "afterRestart1",
        "beforeRestart2",
        "afterRestart2",
        "beforeDownload",
        "afterDownload",
        "finish",
    ]
    samples = []
    for stage in stages:
        generation = 2 if stage in stages[7:] else 1 if stage in stages[5:] else 0
        containers = ["api1"] if stage in stages[:3] else ["api1", "api2"]
        samples.append(
            {
                "stage": stage,
                "at": "2026-10-19T00:00:00+00:00",
                "processes": [
                    {
                        "container": c,
                        "generation": c + ":" + str(generation),
                        "rssKiB": 100000,
                        "highWaterKiB": 110000,
                    }
                    for c in containers
                ],
            }
        )
    report["observations"].update(
        samples=samples,
        processHighWaterKiB={
            p["generation"]: p["highWaterKiB"] for s in samples for p in s["processes"]
        },
        memoryAllowance={"perProcessKiB": 262144, "transferGrowthKiB": 65536},
    )
    report["replicas"] = ["api1", "api2"]
    return report


def test_scale_report_rejects_loss_and_reduced_targets():
    from tools.benchmark_cloud_workflows import validate_scale_report

    report = valid_report()
    validate_scale_report(report, "a" * 40, "b" * 64)
    for mutation in (
        lambda r: r.update(records=1000),
        lambda r: r["datasets"]["table"].update(actual=98899),
        lambda r: r["readers"].pop(),
        lambda r: r["readers"][1].update(tableCount=98899),
        lambda r: r["readers"][2].update(inventoryIdsSha256="e" * 64),
        lambda r: r["restarts"].pop(),
        lambda r: r.update(lostAcknowledgedWrites=1),
        lambda r: r["binary"].update(downloadedSha256="d" * 64),
        lambda r: r.update(tenantIsolation=False),
        lambda r: r["observations"].update(samples=[]),
        lambda r: r.update(sourceCommit="e" * 40),
        lambda r: r.pop("requests"),
        lambda r: r["requests"].update(attempts=2),
        lambda r: r["requests"].update(transportFailures=1),
        lambda r: r["requests"].update(serverErrorResponses=1),
    ):
        bad = copy.deepcopy(report)
        mutation(bad)
        with pytest.raises(ValueError):
            validate_scale_report(bad, "a" * 40, "b" * 64)


def test_current_cloud_scale_report():
    from tools.benchmark_cloud_workflows import validate_scale_report
    from tools.verify_cloud_workflows import source_identity

    path = Path("work/cloud/scale.json")
    assert path.is_file(), "Run tools.benchmark_cloud_workflows at the full approved scale first"
    validate_scale_report(json.loads(path.read_text(encoding="utf-8")), *source_identity())


@pytest.mark.parametrize(
    "fault",
    [
        "empty",
        "zero",
        "huge",
        "missing-upload",
        "lost-generation",
        "upload-growth",
        "missing-replica",
    ],
)
def test_scale_memory_gate_rejects_unbounded_or_incomplete_observations(fault):
    from tools.benchmark_cloud_workflows import validate_scale_report

    report = valid_report()
    observation = report["observations"]
    if fault == "empty":
        observation.update(samples=[{}], processHighWaterKiB=[0])
    elif fault == "zero":
        observation["samples"][0]["processes"][0]["rssKiB"] = 0
    elif fault == "huge":
        for sample in observation["samples"]:
            for process in sample["processes"]:
                process["highWaterKiB"] = 2**40
        observation["processHighWaterKiB"] = dict.fromkeys(
            observation["processHighWaterKiB"], 2**40
        )
    elif fault == "missing-upload":
        observation["samples"].pop(2)
    elif fault == "lost-generation":
        observation["processHighWaterKiB"].pop("api1:0")
    elif fault == "upload-growth":
        for sample in observation["samples"][2:]:
            for process in sample["processes"]:
                if process["generation"] == "api1:0":
                    process["highWaterKiB"] = 200000
        observation["processHighWaterKiB"]["api1:0"] = 200000
    else:
        observation["samples"][-1]["processes"].pop()
    with pytest.raises(ValueError):
        validate_scale_report(report, "a" * 40, "b" * 64)
