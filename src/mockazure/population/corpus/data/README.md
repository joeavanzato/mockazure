# Corpus assets and format limits

`themes.json` is original synthetic MockAzure content. Renderers never read paths
or URLs supplied by a profile.

The unmodified **Noto Sans Regular** font is distributed under the SIL Open Font
License 1.1 in `LICENSE.font`.

- Repository: https://github.com/notofonts/noto-fonts
- Revision: `ffebf8c1ee449e544955a7e813c54f9b73848eac`
- Source: `hinted/ttf/NotoSans/NotoSans-Regular.ttf`
- Font SHA256: `b85c38ecea8a7cfb39c24e395a4007474fa5a4fc864f6ee33309eb4948d232d5`
- License SHA256: `0dab92d0544f7b233403f14b84a663bdbfa746982eda629e7f4f9ffe1b036feb`

PDF generation embeds a font subset and rejects characters without a glyph;
the initial PDF corpus supports Latin, Greek and Cyrillic business text. It is
not a general script-shaping engine. TXT/CSV/JSON/Office preserve Unicode.
CSV prefixes formula-like field values with an apostrophe for safe spreadsheet
opening; JSON preserves original field values exactly.
XLSX fields exceeding 32,767 UTF-16 units are rejected rather than truncated.

Documents are bounded to 8 MiB with a 512 KiB input-field budget. Office packages
contain no macros, external relationships or executable fields. Bytes and metadata
are deterministic for the pinned dependency versions, input, seed and revision.
Existing opaque streaming uploads remain available for larger arbitrary files.

Implementation references:

- [Microsoft's PresentationML structure](https://learn.microsoft.com/en-us/office/open-xml/presentation/structure-of-a-presentationml-document)
- [Spreadsheet inline strings](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.spreadsheet.inlinestring)
- [ReportLab TrueType font support](https://docs.reportlab.com/reportlab/userguide/ch3_fonts/)
