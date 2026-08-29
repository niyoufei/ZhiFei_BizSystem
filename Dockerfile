ARG PYTHON_VERSION=3.12.13
FROM python:${PYTHON_VERSION}-slim-bookworm

ARG APP_VERSION=1.1.0-rc.1

LABEL org.opencontainers.image.title="QingTian ZhiFei BizSystem" \
      org.opencontainers.image.description="Explainable construction tender scoring service" \
      org.opencontainers.image.source="https://github.com/niyoufei/ZhiFei_BizSystem" \
      org.opencontainers.image.version="${APP_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    QINGTIAN_ENV=production \
    QINGTIAN_DATA_DIR=/var/lib/qingtian \
    QINGTIAN_STORAGE_BACKEND=sqlite \
    QINGTIAN_SQLITE_PATH=/var/lib/qingtian/qingtian.sqlite3 \
    PORT=8000

RUN apt-get update \
    && apt-get install --no-install-recommends -y libgomp1 tesseract-ocr tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 qingtian \
    && useradd --uid 10001 --gid qingtian --create-home --shell /usr/sbin/nologin qingtian

WORKDIR /srv/qingtian

COPY requirements-runtime.txt ./
RUN python -m pip install --requirement requirements-runtime.txt

COPY --chown=qingtian:qingtian app ./app
COPY --chown=qingtian:qingtian config ./config
COPY --chown=qingtian:qingtian scripts/container_entrypoint.py scripts/container_healthcheck.py ./scripts/

RUN mkdir -p /var/lib/qingtian \
    && chown -R qingtian:qingtian /var/lib/qingtian /srv/qingtian

USER 10001:10001
EXPOSE 8000
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "scripts/container_healthcheck.py"]

ENTRYPOINT ["python", "-m", "scripts.container_entrypoint"]
