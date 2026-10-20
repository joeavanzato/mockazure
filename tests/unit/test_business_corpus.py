import csv
import hashlib
import io
import json
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

import pytest

from mockazure.population.corpus.models import ContentSpec
from mockazure.population.corpus.render import render

FORMATS = ("txt", "csv", "json", "pdf", "docx", "xlsx", "pptx")
UNICODE = "Résumé – Αθήνα – Москва"


def specification(format, **changes):
    return ContentSpec(
        **{
            "logical_key": "project:0:quarterly-review",
            "format": format,
            "theme": "finance",
            "seed": 42,
            "revision": 1,
            "fields": {
                "title": UNICODE,
                "owner": "Alex",
                "note": "",
                "body": "Budget < 200 & > 50",
            },
            **changes,
        }
    )


@pytest.mark.parametrize("format", FORMATS)
def test_render_is_byte_identical(format):
    first = render(specification(format))
    data = b"".join(first.chunks())
    assert data == b"".join(first.chunks()) == b"".join(render(specification(format)).chunks())
    assert len(data) == first.length
    assert hashlib.sha256(data).hexdigest() == first.sha256
    assert first.filename.endswith("." + format)
    assert len(data) <= 8 * 1024 * 1024
    assert data != b"".join(render(specification(format, revision=2)).chunks())
    reordered = specification(
        format, fields=dict(reversed(list(specification(format).fields.items())))
    )
    assert data == b"".join(render(reordered).chunks())


@pytest.mark.parametrize(
    "format,main",
    [
        ("docx", "word/document.xml"),
        ("xlsx", "xl/workbook.xml"),
        ("pptx", "ppt/presentation.xml"),
    ],
)
def test_office_parts_and_relationships(format, main):
    with zipfile.ZipFile(io.BytesIO(b"".join(render(specification(format)).chunks()))) as archive:
        names = set(archive.namelist())
        assert main in names
        assert len(names) == len(archive.namelist())
        assert all(info.date_time == (2000, 1, 1, 0, 0, 0) for info in archive.infolist())
        assert all("vba" not in name.lower() for name in names)
        types = ET.fromstring(archive.read("[Content_Types].xml"))
        assert any(node.get("PartName") == "/" + main for node in types)
        for name in names:
            if name.endswith((".xml", ".rels")):
                ET.fromstring(archive.read(name))
            if name.endswith(".rels"):
                directory = posixpath.dirname(posixpath.dirname(name))
                for relationship in ET.fromstring(archive.read(name)):
                    assert relationship.get("TargetMode") != "External"
                    target = relationship.attrib["Target"]
                    assert posixpath.normpath(posixpath.join(directory, target)) in names
        combined = "".join(
            "".join(ET.fromstring(archive.read(name)).itertext())
            for name in names
            if name.endswith(".xml")
        )
        assert UNICODE in combined
        assert "Budget < 200 & > 50" in combined


def test_pdf_offsets_and_unicode():
    data = b"".join(render(specification("pdf")).chunks())
    assert data.startswith(b"%PDF-")
    xref = int(re.search(rb"startxref\s+(\d+)", data).group(1))
    assert data[xref : xref + 4] == b"xref"
    assert b"/FontFile2" in data and b"/ToUnicode" in data
    assert b"/JavaScript" not in data and b"/OpenAction" not in data
    for offset in re.findall(rb"^(\d{10}) 00000 n", data, flags=re.M):
        assert re.match(rb"\d+ 0 obj", data[int(offset) :])


def test_csv_json_preserve_unicode_and_do_not_turn_fields_into_formulas():
    fields = {"title": UNICODE, "body": '=HYPERLINK("https://example.invalid")'}
    rows = list(
        csv.reader(
            io.StringIO(b"".join(render(specification("csv", fields=fields)).chunks()).decode())
        )
    )
    assert ["title", UNICODE] in rows
    assert ["body", "'" + fields["body"]] in rows
    document = json.loads(b"".join(render(specification("json", fields=fields)).chunks()))
    assert document["fields"] == fields


@pytest.mark.parametrize(
    "changes",
    [
        {"format": "exe"},
        {"theme": "unknown"},
        {"revision": 0},
        {"seed": True},
        {"logical_key": "../escape"},
        {"fields": {"body": "\x00"}},
        {"fields": {"body": "x" * 65537}},
        {"url": "https://example.invalid/document"},
    ],
)
def test_content_spec_is_bounded_and_not_a_file_or_network_reader(changes):
    with pytest.raises(ValueError):
        specification("txt", **changes) if "format" not in changes else ContentSpec(
            **{**specification("txt").model_dump(), **changes}
        )


def test_pdf_unsupported_glyph_is_explicit():
    with pytest.raises(ValueError, match="glyph"):
        render(specification("pdf", fields={"title": "\U0001f680"}))


def test_excel_cell_limit_rejects_instead_of_silently_truncating():
    with pytest.raises(ValueError, match="cell"):
        render(specification("xlsx", fields={"body": "a" * 32768}))
    with pytest.raises(ValueError, match="cell"):
        render(specification("xlsx", fields={"body": "\U0001f680" * 16384}))


def test_corpus_recipe_produces_small_declarative_spec():
    from mockazure.population.generators import generate, inventory

    values = generate(
        "recipe.business-content",
        {
            "tenant": "11111111-1111-4111-8111-111111111111",
            "namespace": "engineering",
            "referenceTime": "2026-10-20T12:00:00Z",
            "format": "xlsx",
            "theme": "engineering",
        },
        {},
        offset=0,
        limit=1,
    )
    assert inventory()["recipe.business-content"]["family"] == "content.objects"
    assert values[0].operation == "render"
    spec = ContentSpec.model_validate(values[0].payload)
    assert spec.format == "xlsx"
    assert spec.theme == "engineering"
    assert "bytes" not in values[0].payload
