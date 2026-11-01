# syntax=docker/dockerfile:1.7
# ---- build: compile wheels in an isolated stage so the runtime image has no toolchain
FROM python:3.15-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

# ---- runtime
FROM python:3.15-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SENTINEL_LOG_FORMAT=json
RUN useradd --create-home --uid 10001 sentinel
WORKDIR /app
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
COPY --chown=sentinel sentinel.yml ./
COPY --chown=sentinel checks ./checks
USER sentinel
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/healthz').status!=200)"
# PORT is injected by Render/Railway-style hosts; default 8000 locally.
CMD ["sh", "-c", "sentinel serve --port ${PORT:-8000}"]
