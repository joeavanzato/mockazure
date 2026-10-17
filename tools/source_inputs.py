"""Shared byte inputs for product, packaging and qualification attestations."""

from pathlib import Path


def qualification_files(root):
    """Include all resource suffixes and the actual native network guard.

    Interpreter caches are generated execution state, never source inputs.
    Runtime instances and vendor archives live outside these bounded trees.
    """
    root = Path(root)
    return {
        path
        for directory in ("src/mockazure", "integrations", "tools", "deploy", "tests/support")
        for path in (root / directory).rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    }
