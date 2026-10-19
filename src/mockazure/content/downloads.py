"""Keep uploaded bytes inert when a browser opens a same-origin download."""

from uuid import UUID


def safe_download_headers(identifier):
    return {
        "Content-Disposition": f'attachment; filename="{UUID(str(identifier))}.bin"',
        "Content-Security-Policy": "sandbox; default-src 'none'",
        "X-Content-Type-Options": "nosniff",
    }
