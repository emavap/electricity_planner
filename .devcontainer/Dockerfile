# Multi-arch (amd64 / arm64) dev container for the Electricity Planner HA integration.
FROM mcr.microsoft.com/devcontainers/python:3.12

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps useful for HA test dependencies + native wheels on arm64.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential \
      git \
      curl \
      libffi-dev \
      libssl-dev \
      libxml2-dev \
      libxslt1-dev \
      libjpeg-dev \
      zlib1g-dev \
      make \
      cmake \
 && rm -rf /var/lib/apt/lists/*

# Upgrade build toolchain before installing deps (avoids setuptools/pkg_resources issues with newer Python)
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

WORKDIR /workspace

COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install --no-cache-dir -r /tmp/requirements-dev.txt \
 && pip install --no-cache-dir pre-commit commitizen

CMD ["sleep", "infinity"]
