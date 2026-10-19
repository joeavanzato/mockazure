import json

from typer.testing import CliRunner

from mockazure.cli import app


def test_profile_cli_resolves_and_refuses_overwrite(tmp_path):
    bundle = tmp_path / "profiles"
    bundle.mkdir()
    (bundle / "tenant.yaml").write_text("kind: tenant\nname: base\n")
    (bundle / "template.json").write_text(
        json.dumps(
            {
                "kind": "template",
                "name": "lab",
                "tenantProfile": "base",
                "parameters": {"seed": 42, "referenceTime": "2026-10-01T00:00:00Z"},
            }
        )
    )
    runner = CliRunner()
    assert runner.invoke(app, ["profile", "validate", "--bundle", str(bundle)]).exit_code == 0
    output = tmp_path / "resolved.json"
    args = [
        "profile",
        "resolve",
        "--bundle",
        str(bundle),
        "--template",
        "lab",
        "--output",
        str(output),
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    saved = output.read_bytes()
    assert json.loads(saved)["seed"] == 42
    assert runner.invoke(app, args).exit_code != 0
    assert output.read_bytes() == saved


def test_profile_cli_rejects_invalid_input_without_traceback(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("!!python/object:danger {}")
    result = CliRunner().invoke(app, ["profile", "validate", "--bundle", str(path)])
    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_exported_aggregate_bundle_can_exceed_one_document_limit(tmp_path):
    import json

    from mockazure.commands.profiles import read_bundle

    documents = {
        name: {
            "schemaVersion": 1,
            "kind": "data",
            "name": name,
            "resources": {
                "item": {"family": "content.objects", "properties": {"text": "x" * 600000}}
            },
        }
        for name in ("first", "second")
    }
    path = tmp_path / "export.json"
    path.write_text(json.dumps({"documents": documents}), encoding="utf-8")
    assert set(read_bundle(path).documents) == {"first", "second"}
