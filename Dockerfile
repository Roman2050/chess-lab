# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e

# linux/amd64 manifest digests. Update tags and digests together after verification.
FROM docker.io/library/gcc:12.5.0-bookworm@sha256:e3bbedbccf19eb1ac42b41f26422b4b255a7bfc02c686a9e1f49f4b3b977a405 AS stockfish-builder

SHELL ["/bin/bash", "-euxo", "pipefail", "-c"]

# Stockfish 18 (sf_18), exact commit cb3d4ee9b47d0c5aae855b12379378ea1439675c.
# The two NNUE files named by this source revision are downloaded separately and
# verified with their complete SHA-256 digests before the unmodified source is built.
RUN mkdir -p /tmp/stockfish /out \
    && curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
        --output /tmp/stockfish/source.tar.gz \
        https://github.com/official-stockfish/Stockfish/archive/cb3d4ee9b47d0c5aae855b12379378ea1439675c.tar.gz \
    && echo 'b5d3b85e08cdf9189a4753142eb21a4333983d97501531b19e1cd1ac9fc43f35  /tmp/stockfish/source.tar.gz' \
        | sha256sum --check --strict - \
    && tar --extract --gzip --file /tmp/stockfish/source.tar.gz --directory /tmp/stockfish \
    && curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
        --output /tmp/stockfish/Stockfish-cb3d4ee9b47d0c5aae855b12379378ea1439675c/src/nn-c288c895ea92.nnue \
        https://tests.stockfishchess.org/api/nn/nn-c288c895ea92.nnue \
    && echo 'c288c895ea924429ea9092e3f36b2b3c1f00f2a3a4c759ff7e57e79e3b43e4a7  /tmp/stockfish/Stockfish-cb3d4ee9b47d0c5aae855b12379378ea1439675c/src/nn-c288c895ea92.nnue' \
        | sha256sum --check --strict - \
    && curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
        --output /tmp/stockfish/Stockfish-cb3d4ee9b47d0c5aae855b12379378ea1439675c/src/nn-37f18f62d772.nnue \
        https://tests.stockfishchess.org/api/nn/nn-37f18f62d772.nnue \
    && echo '37f18f62d772f3107e1d6aaca3898c130c3c86f2ab63e6555fbbca20635a899d  /tmp/stockfish/Stockfish-cb3d4ee9b47d0c5aae855b12379378ea1439675c/src/nn-37f18f62d772.nnue' \
        | sha256sum --check --strict - \
    && make --directory=/tmp/stockfish/Stockfish-cb3d4ee9b47d0c5aae855b12379378ea1439675c/src \
        --jobs="$(nproc)" build ARCH=x86-64 \
    && make --directory=/tmp/stockfish/Stockfish-cb3d4ee9b47d0c5aae855b12379378ea1439675c/src \
        strip ARCH=x86-64 \
    && install --mode=0755 \
        /tmp/stockfish/Stockfish-cb3d4ee9b47d0c5aae855b12379378ea1439675c/src/stockfish \
        /out/stockfish \
    && readelf --file-header /out/stockfish | grep --fixed-strings 'Machine:                           Advanced Micro Devices X86-64' \
    && printf 'uci\nquit\n' | /out/stockfish | grep --fixed-strings 'id name Stockfish 18'


FROM ghcr.io/astral-sh/uv:0.11.3@sha256:c152167fbb12521eafe9328693e88adb7474fbe2f1227576ceea87aeec18683e AS uv-bin


FROM docker.io/library/python:3.12.11-slim-bookworm@sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49 AS application-builder

COPY --from=uv-bin /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE THIRD_PARTY_NOTICES.md ./
COPY app ./app

# Install the project and runtime dependencies exclusively from the committed lock.
RUN uv sync --frozen --no-dev --no-editable

COPY scripts/download_eco.py ./scripts/download_eco.py

# The generator uses a commit-pinned upstream dataset; runtime startup is offline.
RUN .venv/bin/python scripts/download_eco.py


FROM docker.io/library/python:3.12.11-slim-bookworm@sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49 AS runtime

ARG OCI_SOURCE
ARG OCI_REVISION
ARG OCI_VERSION

LABEL org.opencontainers.image.title="Chess Lab" \
      org.opencontainers.image.description="Bulk chess game analysis and opponent scouting with Stockfish-backed insights." \
      org.opencontainers.image.source="${OCI_SOURCE}" \
      org.opencontainers.image.revision="${OCI_REVISION}" \
      org.opencontainers.image.version="${OCI_VERSION}" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STOCKFISH_PATH=/usr/local/bin/stockfish

WORKDIR /app

COPY --from=application-builder /app/.venv /app/.venv
COPY --from=application-builder /app/app /app/app
COPY --from=application-builder /app/data /app/data
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY --from=stockfish-builder /out/stockfish /usr/local/bin/stockfish
COPY LICENSE THIRD_PARTY_NOTICES.md /usr/share/licenses/chess-lab/

# Fail the build when release identity was not supplied explicitly or disagrees
# with the installed package metadata. A full lowercase Git SHA is required.
RUN test -n "${OCI_SOURCE}" \
    && test -n "${OCI_REVISION}" \
    && test -n "${OCI_VERSION}" \
    && test "$(python -c 'from importlib.metadata import version; print(version("chess-lab"))')" = "${OCI_VERSION}" \
    && test "${#OCI_REVISION}" -eq 40 \
    && case "${OCI_REVISION}" in *[!0-9a-f]*) exit 1 ;; esac \
    && python -m compileall -q /app/app /app/alembic \
    && groupadd --gid 10001 chesslab \
    && useradd --uid 10001 --gid chesslab --no-create-home --home-dir /app \
        --shell /usr/sbin/nologin chesslab

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
