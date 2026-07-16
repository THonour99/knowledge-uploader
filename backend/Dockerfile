ARG PYTHON_IMAGE=python:3.11-slim-bookworm

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

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONUTF8=1
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV PATH=/usr/local/bin:$PATH

WORKDIR /app

COPY --from=target-dependencies /install/ /usr/local/
COPY . /app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS development

COPY --from=development-dependencies /install/ /usr/local/
