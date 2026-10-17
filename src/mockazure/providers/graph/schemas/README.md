# Microsoft Graph structural references

These JSON files are mechanically extracted from Microsoft's CSDL, not handwritten schemas.
The source repository, exact commit, path and full-file SHA-256 are embedded in each file.
The source licence is preserved in LICENSE.metadata.

Source: https://github.com/microsoftgraph/msgraph-metadata/tree/34b8e9c29032c82352a6fd7138be0a864a842bb2/schemas

Reproduce offline after acquiring the public source files:

    python tools/extract_graph_schema.py v1.0-Prod.csdl src/mockazure/providers/graph/schemas/v1.0.json --revision 34b8e9c29032c82352a6fd7138be0a864a842bb2 --version v1.0
    python tools/extract_graph_schema.py beta-Prod.csdl src/mockazure/providers/graph/schemas/beta.json --revision 34b8e9c29032c82352a6fd7138be0a864a842bb2 --version beta

Extraction retains structural properties, inherited properties, enum members and
nullability for the evidence/licensing roots and their dependencies. It does not
claim that every declared schema property has an implemented producer or query.
Route field allowlists and Microsoft REST operation documentation determine those
capabilities. Navigation properties are not included.

Open types are closed to declared properties for evidence ingestion: arbitrary
extension bags, credentials and executable content are not accepted. Metadata
can include fields not yet described on a resource page; metadata determines
structural types while REST endpoint documentation determines operation behavior.

The local validator bounds ordinary strings to16KiB and encoded audit oldValue/newValue
strings to4MiB. These are local ingestion limits, not CSDL constraints. Audit differences
need the larger bound to represent a valid user with several large licensed plan sets.
