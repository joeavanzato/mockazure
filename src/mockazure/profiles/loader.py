"""Bounded safe YAML/JSON parser. No filesystem or network includes."""

import re

import yaml
from pydantic import ValidationError
from yaml.events import AliasEvent, CollectionEndEvent, CollectionStartEvent, ScalarEvent

from mockazure.profiles.models import ProfileBundle, ProfileDocument

MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
MAX_DOCUMENTS = 128
MAX_DEPTH = 32
MAX_NODES = 100_000


class DataLoader(yaml.SafeLoader):
    yaml_implicit_resolvers = {
        key: [
            (tag, pattern)
            for tag, pattern in entries
            if tag not in {"tag:yaml.org,2002:timestamp", "tag:yaml.org,2002:bool"}
        ]
        for key, entries in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key == "<<" or key in result:
                raise ValueError("Non-string, duplicate or merge key")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


DataLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$"), list("tf")
)


def parse_document(data: bytes, *, remaining_nodes: int = MAX_NODES) -> tuple[dict, int]:
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError("Document exceeds 1 MiB")
    depth, nodes = 0, 0
    try:
        source = data.decode("utf-8-sig")
        for event in yaml.parse(source, Loader=DataLoader):
            if isinstance(event, AliasEvent) or getattr(event, "anchor", None):
                raise ValueError("YAML aliases and anchors are unsupported")
            if getattr(event, "tag", None):
                raise ValueError("Explicit YAML tags are unsupported")
            if isinstance(event, (CollectionStartEvent, ScalarEvent)):
                nodes += 1
                if nodes > remaining_nodes:
                    raise ValueError("Bundle exceeds node limit")
            if isinstance(event, CollectionStartEvent):
                depth += 1
                if depth > MAX_DEPTH:
                    raise ValueError("Document exceeds depth limit")
            elif isinstance(event, CollectionEndEvent):
                depth -= 1
        value = yaml.load(source, Loader=DataLoader)
        if not isinstance(value, dict):
            raise ValueError("A document must be an object")
        return value, nodes
    except (UnicodeError, yaml.YAMLError, RecursionError, ValueError, TypeError):
        raise ValueError("Invalid or unsafe structured document") from None


def load_bundle(documents: dict[str, bytes]) -> ProfileBundle:
    if not 1 <= len(documents) <= MAX_DOCUMENTS:
        raise ValueError("Bundle must contain 1 to 128 documents")
    if sum(len(value) for value in documents.values()) > MAX_BUNDLE_BYTES:
        raise ValueError("Bundle exceeds 16 MiB")
    parsed, remaining = {}, MAX_NODES
    for key, raw in documents.items():
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", key) or ".." in key:
            raise ValueError("Invalid bundle document key")
        try:
            value, nodes = parse_document(raw, remaining_nodes=remaining)
            remaining -= nodes
            document = ProfileDocument.model_validate(value)
        except (ValueError, ValidationError):
            raise ValueError(f"Invalid profile document: {key}") from None
        if document.name in parsed:
            raise ValueError(f"Duplicate profile name: {document.name}")
        parsed[document.name] = document
    return ProfileBundle(documents=parsed)
