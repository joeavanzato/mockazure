import hashlib

from mockazure.content.models import CHUNK_SIZE
from mockazure.population.corpus.models import MAX_BYTES, ContentSpec, RenderedContent
from mockazure.population.corpus.text import render_text

MEDIA = {
    "txt": "text/plain",
    "csv": "text/csv",
    "json": "application/json",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def render(spec: ContentSpec) -> RenderedContent:
    spec = ContentSpec.model_validate(spec.model_dump())
    if spec.format == "pdf":
        from mockazure.population.corpus.pdf import render_pdf

        data = render_pdf(spec)
    elif spec.format in {"docx", "xlsx", "pptx"}:
        from mockazure.population.corpus.office import render_office

        data = render_office(spec)
    else:
        data = render_text(spec)
    if len(data) > MAX_BYTES:
        raise ValueError("Rendered document exceeds 8 MiB; use opaque streaming content")

    def chunks():
        for start in range(0, len(data), CHUNK_SIZE):
            yield data[start : start + CHUNK_SIZE]

    name = spec.logical_key.replace(":", "-")
    return RenderedContent(
        MEDIA[spec.format],
        f"{name}-r{spec.revision}.{spec.format}",
        len(data),
        hashlib.sha256(data).hexdigest(),
        chunks,
    )
