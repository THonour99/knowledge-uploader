ARG PYTHON_IMAGE=python:3.11-slim-bookworm
ARG MINIO_MC_IMAGE=minio/mc:RELEASE.2024-04-18T16-45-29Z@sha256:5a84109d6b29bab96c3122e4a7ba888fbf48d4cdc83bc8bf88e3a7ac67b970b8
ARG VCS_REF=unknown

FROM --platform=$TARGETPLATFORM ${MINIO_MC_IMAGE} AS minio-client

FROM --platform=$BUILDPLATFORM ${PYTHON_IMAGE} AS requirements-source

ARG BUILDPLATFORM
ARG TARGETPLATFORM

WORKDIR /requirements
COPY requirements.txt ./

# Native wheels must be resolved and installed in a target-platform stage.
# Copying site-packages from BUILDPLATFORM would produce invalid ARM64 images.
FROM --platform=$TARGETPLATFORM ${PYTHON_IMAGE} AS target-dependencies

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_NO_CACHE_DIR=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUTF8=1

COPY --from=requirements-source /requirements/requirements.txt /requirements.txt
RUN python -m pip install --prefix=/install --no-cache-dir -r /requirements.txt

FROM --platform=$TARGETPLATFORM ${PYTHON_IMAGE} AS development-dependencies

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_NO_CACHE_DIR=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUTF8=1

COPY requirements.txt requirements-dev.txt /
RUN python -m pip install --prefix=/install --no-cache-dir -r /requirements-dev.txt

FROM --platform=$TARGETPLATFORM ${PYTHON_IMAGE} AS runtime

ARG TARGETPLATFORM
ARG VCS_REF

LABEL org.opencontainers.image.revision=$VCS_REF

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONUTF8=1
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV PATH=/usr/local/bin:$PATH

WORKDIR /app

COPY --from=target-dependencies /install/ /usr/local/
COPY --from=minio-client /usr/bin/mc /usr/local/bin/mc
COPY . /app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS development

COPY --from=development-dependencies /install/ /usr/local/
