ARG PYTHON_IMAGE=python:3.11-slim-bookworm
FROM --platform=$BUILDPLATFORM ${PYTHON_IMAGE} AS builder

ARG PYTHON_IMAGE
ARG TARGETPLATFORM
ARG BUILDPLATFORM

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY requirements.txt requirements-dev.txt ./
RUN pip install --user --no-cache-dir -r requirements-dev.txt

FROM ${PYTHON_IMAGE} AS runtime

ARG PYTHON_IMAGE

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PATH=/root/.local/bin:$PATH

WORKDIR /app

COPY --from=builder /root/.local /root/.local
COPY . /app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
