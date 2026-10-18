"""Select a method offered by the local simulator's browser challenge."""

from html.parser import HTMLParser


class MethodOptions(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_methods = False
        self.method = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select":
            self.in_methods = attrs.get("name") == "method_id"
        elif tag == "option" and self.in_methods and not self.method:
            self.method = attrs.get("value")

    def handle_endtag(self, tag):
        if tag == "select":
            self.in_methods = False


def mfa_form_values(page, values):
    if values.get("decision") != "complete":
        return values
    parser = MethodOptions()
    parser.feed(page)
    # Legacy profiles have no selection. Empty fresh profiles remain server-denied.
    return {**values, "method_id": parser.method} if parser.method else values
