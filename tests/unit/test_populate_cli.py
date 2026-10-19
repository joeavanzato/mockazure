from typer.testing import CliRunner

from mockazure.cli import app


def test_population_commands_are_exposed():
    result = CliRunner().invoke(app, ["populate", "--help"])
    assert result.exit_code == 0
    for name in ("preview", "apply", "status", "resume", "cancel", "manifest"):
        assert name in result.stdout
