"""Identical Windows/IANA zone behavior on all hosts using pinned tzdata and CLDR."""

import json
import re
from functools import lru_cache
from importlib.resources import files
from zoneinfo import ZoneInfo

from mockazure.providers.graph.errors import GraphError


@lru_cache(maxsize=256)
def resolve_zone(name):
    if not isinstance(name, str) or len(name) > 128:
        raise GraphError(400, "ErrorInvalidTimeZone", "A supported time zone is required")
    mapping = json.loads(
        files("mockazure.calendar").joinpath("data/windows-zones.json").read_text("utf-8")
    )
    key = mapping.get(name, name)
    if not re.fullmatch(r"[A-Za-z0-9_+/-]+", key) or any(
        p in {"", ".", ".."} for p in key.split("/")
    ):
        raise GraphError(400, "ErrorInvalidTimeZone", "A supported time zone is required")
    try:
        with files("tzdata.zoneinfo").joinpath(*key.split("/")).open("rb") as handle:
            return ZoneInfo.from_file(handle, key=key)
    except (FileNotFoundError, ValueError, IsADirectoryError):
        raise GraphError(400, "ErrorInvalidTimeZone", "A supported time zone is required") from None
