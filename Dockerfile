FROM python:3.12-slim

ARG HOMESTORAGE_VERSION=3.0.0
ARG HOMESTORAGE_REVISION=local
LABEL org.opencontainers.image.title="HomeStorage" \
    org.opencontainers.image.source="https://github.com/Adventurer0/HomeStorage" \
    org.opencontainers.image.version="${HOMESTORAGE_VERSION}" \
    org.opencontainers.image.revision="${HOMESTORAGE_REVISION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    APP_TIMEZONE=Asia/Shanghai \
    HOMESTORAGE_VERSION=${HOMESTORAGE_VERSION} \
    HOMESTORAGE_REVISION=${HOMESTORAGE_REVISION}

RUN python -c "import os,re; assert re.fullmatch(r'[0-9]+\\.[0-9]+\\.[0-9]+', os.environ['HOMESTORAGE_VERSION']); assert re.fullmatch(r'local|[0-9a-f]{40}', os.environ['HOMESTORAGE_REVISION'])"

WORKDIR /app
COPY requirements.txt ./
# Optional trusted CA for corporate/cloud proxies, available only during this build step.
RUN --mount=type=secret,id=pip_ca,required=false \
    if [ -f /run/secrets/pip_ca ]; then export PIP_CERT=/run/secrets/pip_ca; fi \
    && pip install --no-cache-dir -r requirements.txt \
    && mkdir -p /data \
    && chown 1000:1000 /data
COPY app ./app
COPY updater ./updater
COPY 交接文档.md ./HANDOVER.md
RUN chmod -R a+rX /app/app /app/updater && chmod a+r /app/HANDOVER.md

USER 1000:1000
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-proxy-headers"]
