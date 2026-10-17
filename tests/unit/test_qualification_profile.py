from pathlib import Path

import pytest


def test_release_profiles_are_distinct_and_keep_predecessor_defaults(monkeypatch):
    from tools.qualification_profile import STAGES, profile_for

    monkeypatch.delenv("MOCKAZURE_QUALIFICATION_PROFILE", raising=False)
    defaults = [profile_for(stage) for stage in STAGES]
    assert all(p.runtime_dir == ".runtime/" + p.stage for p in defaults)
    monkeypatch.setenv("MOCKAZURE_QUALIFICATION_PROFILE", "release")
    profiles = [profile_for(stage) for stage in STAGES]
    ports = [port for p in profiles for port in p.ports]
    assert len(ports) == len(set(ports))
    assert not set(ports) & {port for p in defaults for port in p.ports}
    assert len({p.project for p in profiles}) == len(profiles)
    for p in profiles:
        assert Path(p.runtime_dir).parts[:1] == (".runtime",)
        assert ".." not in Path(p.runtime_dir).parts
        assert p.runtime_dir.startswith(".runtime/m6-")
        assert p.project.startswith("mockazure-m6-")
        assert p.compose.startswith("compose.release.")
        assert p.image.startswith("mockazure:m6-")
        assert p.text("mockazure:" + p.surface) == p.image
    with pytest.raises(ValueError):
        profile_for("../unknown")
    monkeypatch.setenv("MOCKAZURE_QUALIFICATION_PROFILE", "production")
    with pytest.raises(ValueError):
        profile_for("m5")


def test_profile_templates_do_not_change_credentials_or_cloud_resource_audiences(monkeypatch):
    from tools.qualification_profile import profile_for

    monkeypatch.setenv("MOCKAZURE_QUALIFICATION_PROFILE", "release")
    p = profile_for("m5")
    secret = "x36443_55444_fake_local_credential"
    original = f"postgresql://mockazure_m5:{secret}@127.0.0.1:55444/mockazure_m5"
    changed = p.text(original)
    assert secret in changed
    assert "127.0.0.1:55450/" in changed
    assert "mockazure_m6_arm" in changed
    assert p.text("https://management.azure.com/") == "https://management.azure.com/"
    assert p.text("compose.clients.m5.yaml") == p.clients_compose
    assert p.text("https://localhost:36444") == "https://localhost:46444"
    assert p.port(36445) == 46445
    assert p.port(8443) == 8443


def test_profile_endpoint_guard_rejects_nonlocal_or_unconfigured_origins(monkeypatch):
    from tools.qualification_profile import profile_for

    monkeypatch.setenv("MOCKAZURE_QUALIFICATION_PROFILE", "release")
    p = profile_for("m5")
    p.require_local_origin("https://localhost:46443")
    for url in (
        "https://graph.microsoft.com",
        "https://localhost:36443",
        "http://localhost:46443",
        "https://localhost:46443.evil.test",
        "https://user@localhost:46443",
        "https://localhost:46443/path",
        "https://localhost:46443#fragment",
    ):
        with pytest.raises(ValueError):
            p.require_local_origin(url)


def test_release_ports_do_not_overlap_any_preserved_compose_listener(monkeypatch):
    import re

    from tools.qualification_profile import STAGES, profile_for

    monkeypatch.setenv("MOCKAZURE_QUALIFICATION_PROFILE", "release")
    preserved = {
        int(port)
        for path in Path.cwd().glob("compose*.yaml")
        if "release." not in path.name
        for port in re.findall(r"127\.0\.0\.1:(\d+):", path.read_text(encoding="utf-8"))
    }
    release = {port for stage in STAGES for port in profile_for(stage).ports}
    assert not release & preserved
