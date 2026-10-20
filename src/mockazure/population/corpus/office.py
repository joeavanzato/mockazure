"""Small macro-free OOXML packages with fixed part order and timestamps."""

import io
import textwrap
import zipfile
from xml.sax.saxutils import escape

from mockazure.population.corpus.text import paragraphs, rows

R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
CT = "application/vnd.openxmlformats-officedocument."
DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


def relationships(values):
    return (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{i}" Type="{R}/{kind}" Target="{target}"/>'
            for i, (kind, target) in enumerate(values, 1)
        )
        + "</Relationships>"
    )


def docx(spec):
    body = "".join(
        '<w:p><w:r><w:t xml:space="preserve">' + escape(line) + "</w:t></w:r></w:p>"
        for paragraph in paragraphs(spec)
        for line in paragraph.splitlines() or [""]
    )
    return {
        "word/document.xml": (
            f'<w:document xmlns:w="{W}"><w:body>{body}'
            '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
            "</w:sectPr></w:body></w:document>",
            "wordprocessingml.document.main+xml",
        )
    }


def xlsx(spec):
    sheet_rows = []
    for index, row in enumerate([("field", "value"), *rows(spec)], 1):
        if any(len(value.encode("utf-16-le")) // 2 > 32767 for value in row):
            raise ValueError("Excel cell exceeds 32767 UTF-16 units")
        cells = "".join(
            f'<c r="{column}{index}" t="inlineStr"><is><t xml:space="preserve">'
            f"{escape(value)}</t></is></c>"
            for column, value in zip(("A", "B"), row, strict=True)
        )
        sheet_rows.append(f'<row r="{index}">{cells}</row>')
    return {
        "xl/workbook.xml": (
            f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets>'
            '<sheet name="Business" sheetId="1" r:id="rId1"/></sheets></workbook>',
            "spreadsheetml.sheet.main+xml",
        ),
        "xl/_rels/workbook.xml.rels": (
            relationships([("worksheet", "worksheets/sheet1.xml")]),
            None,
        ),
        "xl/worksheets/sheet1.xml": (
            f'<worksheet xmlns="{S}"><cols><col min="1" max="1" width="20" customWidth="1"/>'
            '<col min="2" max="2" width="90" customWidth="1"/></cols>'
            "<sheetData>" + "".join(sheet_rows) + "</sheetData></worksheet>",
            "spreadsheetml.worksheet+xml",
        ),
    }


def group():
    return (
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
    )


def theme():
    colors = [
        ("dk1", "172B4D"),
        ("lt1", "FFFFFF"),
        ("dk2", "334155"),
        ("lt2", "F1F5F9"),
        ("accent1", "2563EB"),
        ("accent2", "0D9488"),
        ("accent3", "D97706"),
        ("accent4", "7C3AED"),
        ("accent5", "DB2777"),
        ("accent6", "475569"),
        ("hlink", "2563EB"),
        ("folHlink", "7C3AED"),
    ]
    palette = "".join(f'<a:{key}><a:srgbClr val="{color}"/></a:{key}>' for key, color in colors)
    fonts = "".join(
        f'<a:{kind}><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/></a:{kind}>'
        for kind in ("majorFont", "minorFont")
    )
    fill = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    line = (
        '<a:ln w="9525" cap="flat" cmpd="sng" algn="ctr">'
        + fill
        + '<a:prstDash val="solid"/><a:miter lim="800000"/></a:ln>'
    )
    return (
        f'<a:theme xmlns:a="{A}" name="MockAzure"><a:themeElements>'
        f'<a:clrScheme name="Business">{palette}</a:clrScheme>'
        f'<a:fontScheme name="Business">{fonts}</a:fontScheme>'
        f'<a:fmtScheme name="Business"><a:fillStyleLst>{fill * 3}</a:fillStyleLst>'
        f"<a:lnStyleLst>{line * 3}</a:lnStyleLst><a:effectStyleLst>"
        + "<a:effectStyle><a:effectLst/></a:effectStyle>"
        * 3
        + f"</a:effectStyleLst><a:bgFillStyleLst>{fill * 3}</a:bgFillStyleLst>"
        "</a:fmtScheme></a:themeElements></a:theme>"
    )


def pptx(spec):
    lines = [
        line
        for paragraph in paragraphs(spec)
        for original in paragraph.splitlines() or [""]
        for line in textwrap.wrap(original, 80, replace_whitespace=False) or [""]
    ]
    pages = [lines[start : start + 12] for start in range(0, len(lines), 12)]
    namespaces = f'xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"'
    clr = (
        'accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" '
        'accent5="accent5" accent6="accent6" bg1="lt1" bg2="lt2" '
        'folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"'
    )
    slide_ids = "".join(
        f'<p:sldId id="{255 + i}" r:id="rId{i + 2}"/>' for i in range(1, len(pages) + 1)
    )
    parts = {
        "ppt/presentation.xml": (
            f"<p:presentation {namespaces}><p:sldMasterIdLst>"
            '<p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
            f'<p:sldIdLst>{slide_ids}</p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/>'
            '<p:notesSz cx="6858000" cy="9144000"/></p:presentation>',
            "presentationml.presentation.main+xml",
        ),
        "ppt/_rels/presentation.xml.rels": (
            relationships(
                [
                    ("slideMaster", "slideMasters/slideMaster1.xml"),
                    ("presProps", "presProps.xml"),
                    *[("slide", f"slides/slide{i}.xml") for i in range(1, len(pages) + 1)],
                ]
            ),
            None,
        ),
        "ppt/presProps.xml": (f"<p:presentationPr {namespaces}/>", "presentationml.presProps+xml"),
        "ppt/slideMasters/slideMaster1.xml": (
            f"<p:sldMaster {namespaces}><p:cSld><p:spTree>{group()}</p:spTree></p:cSld>"
            f"<p:clrMap {clr}/><p:sldLayoutIdLst>"
            '<p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
            "<p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles></p:sldMaster>",
            "presentationml.slideMaster+xml",
        ),
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": (
            relationships(
                [
                    ("slideLayout", "../slideLayouts/slideLayout1.xml"),
                    ("theme", "../theme/theme1.xml"),
                ]
            ),
            None,
        ),
        "ppt/slideLayouts/slideLayout1.xml": (
            f'<p:sldLayout {namespaces} type="blank" preserve="1"><p:cSld name="Blank">'
            f"<p:spTree>{group()}</p:spTree></p:cSld>"
            "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>",
            "presentationml.slideLayout+xml",
        ),
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": (
            relationships([("slideMaster", "../slideMasters/slideMaster1.xml")]),
            None,
        ),
        "ppt/theme/theme1.xml": (theme(), "theme+xml"),
    }
    for index, page in enumerate(pages, 1):
        body = "".join(
            '<a:p><a:r><a:rPr lang="en-US" sz="1800"/><a:t>'
            + escape(line)
            + '</a:t></a:r><a:endParaRPr lang="en-US"/></a:p>'
            for line in page
        )
        parts[f"ppt/slides/slide{index}.xml"] = (
            f"<p:sld {namespaces}><p:cSld><p:spTree>{group()}<p:sp>"
            '<p:nvSpPr><p:cNvPr id="2" name="Business content"/><p:cNvSpPr txBox="1"/>'
            '<p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="457200" y="457200"/>'
            '<a:ext cx="11277600" cy="5943600"/></a:xfrm><a:prstGeom prst="rect">'
            "<a:avLst/></a:prstGeom><a:noFill/></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/>"
            f"{body}</p:txBody></p:sp></p:spTree></p:cSld>"
            "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>",
            "presentationml.slide+xml",
        )
        parts[f"ppt/slides/_rels/slide{index}.xml.rels"] = (
            relationships([("slideLayout", "../slideLayouts/slideLayout1.xml")]),
            None,
        )
    return parts


def render_office(spec):
    parts = {"docx": docx, "xlsx": xlsx, "pptx": pptx}[spec.format](spec)
    main = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml", "pptx": "ppt/presentation.xml"}[
        spec.format
    ]
    parts["_rels/.rels"] = (relationships([("officeDocument", main)]), None)
    overrides = "".join(
        f'<Override PartName="/{name}" ContentType="{CT}{kind}"/>'
        for name, (_, kind) in sorted(parts.items())
        if kind
    )
    parts["[Content_Types].xml"] = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        f'<Default Extension="xml" ContentType="application/xml"/>{overrides}</Types>',
        None,
    )
    stream = io.BytesIO()
    # Stored entries avoid compressed-byte variation between zlib versions.
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, (xml, _) in sorted(parts.items()):
            info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            info.create_system = 0
            info.external_attr = 0x20
            archive.writestr(info, (DECL + xml).encode("utf-8"))
    return stream.getvalue()
