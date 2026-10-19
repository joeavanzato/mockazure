"""Ordered inheritance and overlays with deterministic parameter resolution."""

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime

from pydantic import JsonValue

from mockazure.profiles.models import (
    ParameterDefinition,
    ProfileBundle,
    ResolvedProfile,
    ResourceDefinition,
    StepDefinition,
)


def merge(left, right):
    result = deepcopy(left)
    for key, value in right.items():
        result[key] = (
            merge(result[key], value)
            if isinstance(result.get(key), dict) and isinstance(value, dict)
            else deepcopy(value)
        )
    return result


def ancestry(bundle, name, kind, active=()):
    if name in active:
        raise ValueError("Profile inheritance cycle")
    document = bundle.documents.get(name)
    if document is None or document.kind != kind:
        raise ValueError("Missing profile reference or wrong profile kind")
    parents = ancestry(bundle, document.extends, kind, (*active, name)) if document.extends else []
    return [*parents, document]


def mark_origins(value, path, origin, target):
    if isinstance(value, dict) and value:
        for key, child in value.items():
            escaped = key.replace("~", "~0").replace("/", "~1")
            mark_origins(child, path + "/" + escaped, origin, target)
    else:
        target[path] = origin


def parameter_key(key, definitions, scopes=None):
    if key in definitions:
        return key
    if scopes is not None:
        for profile in reversed(scopes):
            if f"{profile}.{key}" in definitions:
                return f"{profile}.{key}"
        candidates = [f"shared.{key}"] if f"shared.{key}" in definitions else []
    else:
        candidates = [name for name in definitions if name.rsplit(".", 1)[-1] == key]
    if len(candidates) != 1:
        raise ValueError("Unknown or ambiguous parameter")
    return candidates[0]


def validate_parameter(key, value, definition):
    accepted = {
        "integer": type(value) is int,
        "number": type(value) in (int, float),
        "string": isinstance(value, str),
        "boolean": type(value) is bool,
    }[definition.type]
    if not accepted or isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"Invalid parameter type: {key}")
    if definition.type in {"number", "integer"} and (
        definition.minimum is not None
        and value < definition.minimum
        or definition.maximum is not None
        and value > definition.maximum
    ):
        raise ValueError(f"Parameter outside bounds: {key}")


def resolve_value(value, parameters, resources):
    if isinstance(value, dict):
        if "$param" in value or "$ref" in value:
            if len(value) != 1:
                raise ValueError("Ambiguous structured reference")
            if "$param" in value:
                key = value["$param"]
                if not isinstance(key, str) or key not in parameters:
                    raise ValueError("Unknown parameter reference")
                return deepcopy(parameters[key])
            if not isinstance(value["$ref"], str) or value["$ref"] not in resources:
                raise ValueError("Missing resource reference")
            return value
        return {key: resolve_value(child, parameters, resources) for key, child in value.items()}
    if isinstance(value, list):
        return [resolve_value(child, parameters, resources) for child in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite numbers are unsupported")
    return value


def order_steps(steps):
    ordered, active = {}, set()

    def visit(key):
        if key in ordered:
            return
        if key not in steps:
            raise ValueError("Missing step dependency")
        if key in active:
            raise ValueError("Step dependency cycle")
        active.add(key)
        for dependency in steps[key].dependsOn:
            visit(dependency)
        active.remove(key)
        ordered[key] = steps[key]

    for key in steps:
        visit(key)
    return ordered


def resolve_template(
    bundle: ProfileBundle, template: str, overrides: dict[str, JsonValue], reference_time: datetime
) -> ResolvedProfile:
    if reference_time.tzinfo is None or reference_time.utcoffset() is None:
        raise ValueError("Reference time requires a timezone")
    templates = ancestry(bundle, template, "template")
    composition = {}
    for document in templates:
        composition = merge(composition, document.model_dump(exclude_unset=True))
    if not composition.get("tenantProfile"):
        raise ValueError("Template must select a tenant profile")
    layers = ancestry(bundle, composition["tenantProfile"], "tenant")
    for name in composition.get("dataProfiles", []):
        layers.extend(ancestry(bundle, name, "data"))
    # Shared parents used by several selected layers are applied once in first-use order.
    layers = list({document.name: document for document in layers}.values())
    definitions = {
        "shared.seed": ParameterDefinition(type="integer", default=0, shared=True),
        "shared.referenceTime": ParameterDefinition(
            type="string", default=reference_time.isoformat(), shared=True
        ),
        "shared.days": ParameterDefinition(type="integer", default=7, minimum=1, shared=True),
    }
    origins, values = {}, {}
    for document in layers:
        for name, definition in document.parameterDefinitions.items():
            key = f"shared.{name}" if definition.shared else f"{document.name}.{name}"
            if key in definitions and definitions[key].type != definition.type:
                raise ValueError("Conflicting parameter definition")
            definitions[key] = (
                ParameterDefinition.model_validate(
                    merge(definitions[key].model_dump(), definition.model_dump(exclude_unset=True))
                )
                if key in definitions
                else definition
            )
            origins[f"/parameters/{key}"] = document.name
    for key, definition in definitions.items():
        values[key] = definition.default
        origins.setdefault(f"/parameters/{key}", "default")
    for document in [*layers, *templates]:
        scopes = (
            [item.name for item in ancestry(bundle, document.name, document.kind)]
            if document.kind != "template"
            else None
        )
        for name, value in document.parameters.items():
            key = parameter_key(name, definitions, scopes)
            values[key] = value
            origins[f"/parameters/{key}"] = document.name
    for name, value in overrides.items():
        key = parameter_key(name, definitions)
        values[key] = value
        origins[f"/parameters/{key}"] = "run"
    for key, value in values.items():
        validate_parameter(key, value, definitions[key])
    try:
        stamp = datetime.fromisoformat(values["shared.referenceTime"].replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("Reference time requires an ISO timestamp with timezone") from None
    merged = {"resources": {}, "steps": {}}
    for document in layers:
        for collection, result in merged.items():
            for key, entry in getattr(document, collection).items():
                patch = entry.model_dump(exclude_unset=True, exclude={"override"})
                old = result.get(key)
                combined = merge(old or {}, patch)
                if old is not None and combined != old and not entry.override:
                    raise ValueError(f"Conflicting {collection}/{key}: explicit override required")
                result[key] = combined
                mark_origins(patch, f"/{collection}/{key}", document.name, origins)
    resources = {
        key: ResourceDefinition.model_validate(resolve_value(value, values, merged["resources"]))
        for key, value in merged["resources"].items()
    }
    steps = {
        key: StepDefinition.model_validate(resolve_value(value, values, resources))
        for key, value in merged["steps"].items()
    }
    result = ResolvedProfile(
        template=template,
        parameters=values,
        resources=resources,
        steps=order_steps(steps),
        origins=origins,
        referenceTime=stamp,
        seed=values["shared.seed"],
        digest="",
    )
    canonical = json.dumps(
        result.model_dump(mode="json", exclude={"digest"}),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return result.model_copy(update={"digest": hashlib.sha256(canonical.encode()).hexdigest()})
