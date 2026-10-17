"""Bounded ARM inventory filters compiled to PostgreSQL predicates."""

from sqlalchemy import and_, func, or_, select, true

from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.models import ArmResource
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.query.ast import Boolean, Compare
from mockazure.providers.graph.query.parser import FilterParser


def invalid(message="Unsupported ARM query"):
    raise ArmError(400, "InvalidQueryParameterValue", message)


def parse_filter(text):
    try:
        return FilterParser(text).parse()
    except GraphError:
        invalid("Invalid or unsupported ARM filter")


def compile_inventory_filter(node, *, groups=False):
    """Tag pairs use one JSONB entry, never unrelated tag names and values."""
    columns = {
        "name": ArmResource.name,
        "location": ArmResource.location,
        "resourcetype": ArmResource.resource_type,
        "resourcegroup": func.split_part(ArmResource.group_key, "/", 5),
    }
    if groups:
        columns = {"location": ArmResource.location}

    def tag_pair(children):
        names = [n for n in children if isinstance(n, Compare) and n.left.name.lower() == "tagname"]
        values = [
            n for n in children if isinstance(n, Compare) and n.left.name.lower() == "tagvalue"
        ]
        if len(names) != 1 or len(values) > 1:
            invalid("A tag filter requires one tagName and at most one tagValue")
        for n in names + values:
            if n.op != "eq" or not isinstance(n.right.value, str):
                invalid("Tag comparisons support string equality")
        tags = func.jsonb_each_text(ArmResource.tags).table_valued("key", "value").alias()
        conditions = [func.lower(tags.c.key) == names[0].right.value.lower()]
        if values:
            conditions.append(tags.c.value == values[0].right.value)
        return select(1).select_from(tags).where(*conditions).correlate(ArmResource).exists()

    def visit(n):
        if isinstance(n, Boolean):
            children = list(n.children)
            if n.op == "and":
                tag_nodes = [
                    c
                    for c in children
                    if isinstance(c, Compare) and c.left.name.lower() in {"tagname", "tagvalue"}
                ]
                terms = [visit(c) for c in children if c not in tag_nodes]
                if tag_nodes:
                    terms.append(tag_pair(tag_nodes))
                return and_(*terms)
            return or_(*(visit(c) for c in children))
        if (
            not isinstance(n, Compare)
            or n.op not in {"eq", "ne"}
            or not isinstance(n.right.value, str)
        ):
            invalid("Only string eq/ne and AND/OR inventory filters are supported")
        if n.left.name.lower() == "tagname":
            return tag_pair([n])
        if n.left.name.lower() not in columns:
            invalid("Unsupported inventory filter property")
        field = func.lower(columns[n.left.name.lower()])
        return field == n.right.value.lower() if n.op == "eq" else field != n.right.value.lower()

    return true() if node is None else visit(node)


def uses_tags(node):
    return (isinstance(node, Compare) and node.left.name.lower() in {"tagname", "tagvalue"}) or (
        isinstance(node, Boolean) and any(uses_tags(c) for c in node.children)
    )
