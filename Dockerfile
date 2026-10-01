# The immutable image digest resolves to Python 3.12.14 on the release baseline.
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

ARG VCS_REF=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/syntheticforge \
    SYNTHETICFORGE_ENV=production \
    SYNTHETICFORGE_HOME=/data \
    SYNTHETICFORGE_APPLICATION_SHA=${VCS_REF} \
    WEB_CONCURRENCY=1

RUN groupadd --system --gid 10001 syntheticforge \
 && useradd --system --uid 10001 --gid syntheticforge --home-dir /home/syntheticforge --create-home syntheticforge \
 && install -d -o syntheticforge -g syntheticforge /app /data /tmp/syntheticforge

WORKDIR /app
COPY requirements-prod.lock /tmp/requirements-prod.lock
RUN python -m pip install --no-cache-dir --requirement /tmp/requirements-prod.lock
COPY README.md pyproject.toml ./
COPY app ./app
COPY scripts/entrypoint.sh /usr/local/bin/syntheticforge-entrypoint
RUN python -m pip install --no-cache-dir --no-build-isolation --no-deps . \
 && chmod 0555 /usr/local/bin/syntheticforge-entrypoint \
 && chown -R syntheticforge:syntheticforge /app /data /home/syntheticforge \
 && rm -f /tmp/requirements-prod.lock

USER 10001:10001
VOLUME ["/data"]
EXPOSE 8000
LABEL org.opencontainers.image.title="SyntheticForge AI" \
      org.opencontainers.image.version="1.0.0-rc1" \
      org.opencontainers.image.revision=${VCS_REF}
HEALTHCHECK --interval=30s --timeout=4s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3).read()"]
STOPSIGNAL SIGTERM
CMD ["/usr/local/bin/syntheticforge-entrypoint"]
