# PSCP broker image. Only the protocol package is copied; it needs nothing beyond the standard library.
# Multi-arch base: runs on Oracle Ampere A1 (arm64) and x86 shapes alike.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY pscp/ ./pscp/

RUN useradd --system --no-create-home pscp
USER pscp

EXPOSE 5050/tcp
# python handles SIGTERM itself (pscp/broker/__main__.py), so `docker stop` shuts down cleanly
ENTRYPOINT ["python", "-m", "pscp.broker"]
CMD ["--host", "0.0.0.0", "--port", "5050"]
