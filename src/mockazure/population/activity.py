"""Random-access public actions: one selected recipe, no expanded step list."""

from copy import deepcopy
from uuid import UUID

from mockazure.population.activity_models import ActivityBatch
from mockazure.population.generators import generate
from mockazure.population.types import Mutation
from mockazure.profiles.identity import stable_id

VERSION = "activity-v1"
SUPPORTED = {
    ("mail.events", "create"),
    ("mail.messages", "create-draft"),
    ("teams.chatMessage", "send-channel"),
    ("teams.chatMessage", "send-reply"),
    ("teams.chatMessage", "send-chat"),
    ("storage.blobs", "upload"),
    ("storage.files", "upload"),
    ("storage.entities", "insert"),
    ("storage.messages", "enqueue"),
    ("keyvault.secrets", "set"),
}


def validate_recipe(snapshot):
    value = snapshot["request"].get("activity")
    if not value:
        return
    batch = ActivityBatch.model_validate(value)
    step = snapshot["resolved"]["steps"].get(batch.recipe_step)
    if (
        not step
        or step["mode"] != "service"
        or (step["family"], step["operation"]) not in SUPPORTED
    ):
        raise ValueError("Finite activity requires a supported public-action recipe step")
    if step.get("target"):
        raise ValueError("Repeated activity cannot supply a single setup resource binding")
    if any(
        batch.recipe_step in other["dependsOn"] for other in snapshot["resolved"]["steps"].values()
    ):
        raise ValueError("Activity must follow setup, not supply dependencies to other steps")


def is_activity_step(snapshot, step):
    batch = snapshot["request"].get("activity")
    return bool(batch and step == snapshot["resolved"]["steps"].get(batch["recipe_step"]))


def step_count(snapshot, key, step):
    batch = snapshot["request"].get("activity")
    return (
        batch["action_count"]
        if batch and batch["recipe_step"] == key
        else step["parameters"].get("count", 1)
    )


def action_at(snapshot: dict, ordinal: int, refs: dict[str, str]) -> Mutation:
    from mockazure.population.planner import parameters_for, resolve_refs

    batch = ActivityBatch.model_validate(snapshot["request"]["activity"])
    if type(ordinal) is not int or not 0 <= ordinal < batch.action_count:
        raise ValueError("Activity ordinal is outside the declared batch")
    if snapshot.get("activity_version") != VERSION:
        raise ValueError("Activity recipe version changed")
    step = snapshot["resolved"]["steps"][batch.recipe_step]
    parameters = resolve_refs(parameters_for(snapshot, step), refs) | {"count": 1}
    mutation = generate(parameters["generator"], parameters, refs, offset=0, limit=1)[0]
    if mutation.operation != step["operation"]:
        raise ValueError("Activity operation differs from its recipe")
    key = f"activity:{batch.recipe_step}:{ordinal}"
    identifier = str(
        stable_id(UUID(snapshot["tenant"]), parameters["namespace"], key, parameters["seed"])
    )
    payload = deepcopy(mutation.payload)
    body = payload.setdefault("body", {})
    family = step["family"]
    if family == "mail.events":
        body["transactionId"] = identifier
        body["subject"] = f"{body.get('subject', 'Business activity')} [{ordinal + 1}]"
    elif family in {"mail.messages", "teams.chatMessage"}:
        if family == "mail.messages":
            body["subject"] = f"{body.get('subject', 'Business activity')} [{ordinal + 1}]"
        else:
            content = body.setdefault("body", {"contentType": "text", "content": "Business update"})
            content["content"] += f" [{ordinal + 1}]"
    elif family == "storage.messages":
        body["text"] += f" [{ordinal + 1}]"
    elif family == "storage.entities":
        body["RowKey"] = identifier
    elif family == "keyvault.secrets":
        payload["variables"]["secret"] = "activity-" + identifier
    elif family in {"storage.blobs", "storage.files"}:
        key_name = "blob" if family == "storage.blobs" else "file"
        original = payload["variables"][key_name]
        suffix = "." + original.rsplit(".", 1)[1] if "." in original else ""
        payload["variables"][key_name] = "activity-" + identifier + suffix
        payload.pop("body", None)
    properties = snapshot["resolved"]["resources"].get(step.get("target"), {}).get("properties", {})
    return mutation.model_copy(
        update={
            "logical_key": key,
            "payload": {**payload, **resolve_refs(properties, refs)},
        }
    )
