import hashlib
import tomllib
from importlib.resources import files
from pathlib import Path


def test_installed_package_contains_hunting_console_and_contract_assets():
    from mockazure.hunting.cookbook import cases

    root = files("mockazure")
    assets = [
        "console/static/hunting.js",
        "hunting/data/tables.json",
        "hunting/data/sources.json",
        "hunting/data/cookbook.json",
        "profiles/data/data/business-security-telemetry.json",
    ]
    assert all(root.joinpath(asset).read_bytes() for asset in assets)
    assert len(cases()) >= 24
    build = tomllib.loads(Path("pyproject.toml").read_text())["tool"]["hatch"]["build"]["targets"]
    assert "/docs/hunting-cookbook.md" in build["sdist"]["include"]
    assert hashlib.sha256(root.joinpath(assets[1]).read_bytes()).hexdigest()
