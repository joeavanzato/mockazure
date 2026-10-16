import hashlib
import hmac
import re
from pathlib import Path


def verify_admin_token(presented: str, token_file: Path) -> bool:
    try:
        expected = token_file.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return False
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", expected):
        return False
    return hmac.compare_digest(
        hashlib.sha256(presented.encode()).digest(), hashlib.sha256(expected.encode()).digest()
    )
