"""Public credential disclosure and bounded relationship projection."""


def serialize_security(payload, query=None):
    selected = query.select if query else None
    result = {key: payload.get(key) for key in selected} if selected else dict(payload)
    if "keyCredentials" in result and (selected is None or "keyCredentials" not in selected):
        result["keyCredentials"] = [dict(item, key=None) for item in result["keyCredentials"]]
    for expansion in query.expand if query else ():
        values = payload.get(expansion.field, [])
        if isinstance(values, dict):
            result[expansion.field] = (
                {key: values.get(key) for key in expansion.select}
                if expansion.select else values
            )
            continue
        if len(values) > 100:
            from mockazure.providers.graph.query.contracts import unsupported

            unsupported("Owner expansion exceeds the supported bound")
        result[expansion.field] = [
            {key: value.get(key) for key in expansion.select} if expansion.select else value
            for value in values
        ]
    return result


def disclose_owners(values, actor):
    if actor.permissions & {
        "Directory.Read.All",
        "Directory.ReadWrite.All",
        "User.Read.All",
        "User.ReadBasic.All",
    }:
        return values
    return [
        {"id": value["id"], "@odata.type": value["@odata.type"]}
        if value["@odata.type"] == "#microsoft.graph.user"
        else value
        for value in values
    ]
