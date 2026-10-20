from uuid import UUID

from mockazure.hunting.contracts import catalog
from mockazure.hunting.schemas import validate_row
from mockazure.population.corpus.models import ContentSpec
from mockazure.population.corpus.render import render
from mockazure.population.generators import generate, inventory
from mockazure.population.generators.business import Business

PARAMS = {
    "tenant": str(UUID(int=142)),
    "namespace": "hunting",
    "seed": 42,
    "referenceTime": "2026-10-07T12:00:00Z",
    "users": 20,
    "days": 7,
    "count": 20,
    "projects": 4,
}
REFS = {"domain": "local.test"}


def rows(table):
    return generate("hunting." + table, PARAMS, REFS, offset=0, limit=20)


def test_every_table_has_nonempty_schema_valid_generation():
    for table in catalog():
        name = "hunting." + table
        assert inventory()[name]["family"] == name
        generated = rows(table)
        assert len(generated) == 20
        for mutation in generated:
            assert mutation.operation == "import"
            validate_row(table, mutation.payload["record"])
        assert generated == rows(table)
        assert generated[10:] == generate(name, PARAMS, REFS, offset=10, limit=10)


def test_identity_device_and_mail_ids_join():
    g = Business(PARAMS, REFS, 0, 1)
    identity = rows("IdentityInfo")[0].payload["record"]
    device = rows("DeviceInfo")[0].payload["record"]
    process = rows("DeviceProcessEvents")[0].payload["record"]
    mail = rows("EmailEvents")[0].payload["record"]
    attachment = rows("EmailAttachmentInfo")[0].payload["record"]
    assert identity["AccountObjectId"] == g.person(0)["id"] == process["AccountObjectId"]
    assert device["AadDeviceId"] == g.id("device", 0)
    assert device["DeviceId"] == process["DeviceId"]
    assert device["DeviceId"] not in {g.id("directory-device", 0), g.id("managed-device", 0)}
    assert mail["NetworkMessageId"] == attachment["NetworkMessageId"] != mail["InternetMessageId"]
    assert mail["InternetMessageId"] == "<" + g.id("internet-message", 0) + "@local.test>"


def test_document_hash_and_alert_evidence_resolve_existing_content():
    g = Business(PARAMS, REFS, 0, 1)
    document = render(ContentSpec.model_validate(g.document(0, 1)))
    attachment = rows("EmailAttachmentInfo")[0].payload["record"]
    file = rows("DeviceFileEvents")[0].payload["record"]
    assert attachment["SHA256"] == file["SHA256"] == document.sha256
    assert attachment["FileSize"] == document.length
    info = rows("AlertInfo")[0].payload["record"]
    evidence = rows("AlertEvidence")[0].payload["record"]
    alert = generate("hunting.alerts", PARAMS, REFS, offset=0, limit=1)[0]
    assert info["AlertId"] == evidence["AlertId"] == alert.payload["id"]
    assert evidence["AccountObjectId"] == g.person(0)["id"]


def test_stacked_business_templates_and_optional_overlays_resolve():
    from datetime import UTC, datetime

    from mockazure.profiles.builtin import builtin_bundle
    from mockazure.profiles.resolver import resolve_template

    for template in ("living-small-business", "living-enterprise", "living-identity-lab"):
        resolved = resolve_template(
            builtin_bundle(), template, {}, datetime(2026, 10, 7, tzinfo=UTC)
        )
        tables = [
            s for s in resolved.steps.values() if s.family in {"hunting." + t for t in catalog()}
        ]
        assert len(tables) == 20
        assert sum(s.parameters["count"] for s in tables) == (
            100000 if template == "living-enterprise" else 400
        )
    for overlay in (
        "suspicious-consent",
        "mailbox-forwarding",
        "unusual-download",
        "privilege-change",
        "unusual-secret-access",
    ):
        mutation = generate(
            "hunting.CloudAppEvents",
            PARAMS | {"overlay": overlay},
            REFS,
            offset=0,
            limit=1,
        )[0]
        assert mutation.payload["record"]["AdditionalFields"] == {
            "simulation": "fixture",
            "overlay": overlay,
        }
        assert mutation.payload["id"] != rows("CloudAppEvents")[0].payload["id"]
