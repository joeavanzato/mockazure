"""Installed starter profiles, loaded through the same strict parser as user bundles."""

import json
from importlib.resources import files

from mockazure.profiles.loader import load_bundle


def builtin_bundle():
    base = files("mockazure.profiles").joinpath("data")
    documents = {}
    for category in ("tenants", "data", "templates"):
        for entry in base.joinpath(category).iterdir():
            if entry.name.endswith(".json"):
                documents[entry.name] = entry.read_bytes()
    return load_bundle(documents)


def catalog_inventory():
    return json.loads(
        files("mockazure.profiles").joinpath("data", "catalog_inventory.json").read_text()
    )
