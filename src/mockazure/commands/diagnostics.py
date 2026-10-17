"""Bounded operational diagnostics: no exception text, SQL, parameters or paths."""

import json
import re

from sqlalchemy.exc import SQLAlchemyError


def failure(component: str, phase: str, error: Exception) -> str:
    category = "invalid_state"
    if isinstance(error, SQLAlchemyError):
        category = "database"
    elif isinstance(error, OSError):
        category = "filesystem"
    value = {"component": component, "phase": phase, "category": category}
    if isinstance(error, SQLAlchemyError):
        state = getattr(getattr(error, "orig", None), "sqlstate", None)
        if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state):
            value["sqlstate"] = state
    if isinstance(error, OSError) and type(error.errno) is int:
        value["errno"] = error.errno
    return json.dumps(value)
