import csv
import io
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def themes():
    return json.loads((Path(__file__).parent / "data/themes.json").read_text(encoding="utf-8"))


def rows(spec):
    return [
        ("dataset", "Synthetic MockAzure business content"),
        ("logical_key", spec.logical_key),
        ("theme", spec.theme),
        ("revision", str(spec.revision)),
        ("seed", str(spec.seed)),
        *sorted({**themes()[spec.theme], **spec.fields}.items()),
    ]


def paragraphs(spec):
    return [f"{key}: {value}" for key, value in rows(spec)]


def render_text(spec):
    if spec.format == "json":
        value = spec.model_dump()
        value["synthetic"] = True
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    if spec.format == "csv":
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\r\n")
        writer.writerow(["field", "value"])
        for row in rows(spec):
            # CSV is often opened in a spreadsheet; preserve dangerous text as literal text.
            writer.writerow(
                [
                    "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
                    for value in row
                ]
            )
        return stream.getvalue().encode()
    return ("\n\n".join(paragraphs(spec)) + "\n").encode()
