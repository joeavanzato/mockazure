import tomllib
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from mockazure.profiles.builtin import builtin_bundle
from mockazure.profiles.resolver import resolve_template


def test_packaged_console_and_builtins():
    bundle = builtin_bundle()
    templates = {"developer-lab", "collection-lab", "investigation-lab", "volume-lab"}
    assert templates <= set(bundle.documents)
    for name in templates:
        assert resolve_template(bundle, name, {}, datetime.now(UTC)).steps
    base = files("mockazure.console").joinpath("static")
    for name in (
        "index.html",
        "styles.css",
        "api.js",
        "app.js",
        "resources.js",
        "profiles.js",
        "runs.js",
        "inspector.js",
    ):
        assert base.joinpath(name).read_bytes()
    build = tomllib.loads(Path("pyproject.toml").read_text())["tool"]["hatch"]["build"]["targets"][
        "sdist"
    ]["include"]
    assert "/examples" in build and "/docs/getting-started.md" in build
