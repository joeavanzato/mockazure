"""Create the dedicated test database credential without printing it."""

import secrets
from pathlib import Path

from mockazure.files import write_private

directory = Path(__file__).resolve().parents[1] / ".runtime"
directory.mkdir(exist_ok=True)
path = directory / "test-db.password"
if not path.exists():
    write_private(path, (secrets.token_urlsafe(32) + "\n").encode())
print("Test database credential is ready")
