FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS build
WORKDIR /build
RUN pip install --no-cache-dir uv==0.12.1
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src ./src
RUN uv export --quiet --frozen --no-dev --no-emit-project --format requirements-txt -o requirements.txt \
    && uv build --out-dir /dist \
    && uv venv /opt/venv \
    && uv pip install --python /opt/venv/bin/python --require-hashes -r requirements.txt \
    && uv pip install --python /opt/venv/bin/python --no-deps /dist/*.whl

FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /project
ENTRYPOINT ["mockazure"]
CMD ["--help"]
