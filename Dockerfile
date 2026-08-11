# Single command on a clean machine:
#   docker build -t pagewatch . && docker run --rm pagewatch --demo
FROM python:3.12-slim AS base

# Dependencies first, so a code change doesn't invalidate the pip layer.
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pagewatch/ ./pagewatch/
COPY fixtures/ ./fixtures/
COPY watches/ ./watches/

# Don't run as root. A monitor fetches untrusted HTML; there is no reason for it
# to have more privilege than reading its own config.
RUN useradd --create-home --uid 10001 watcher \
 && mkdir -p /data && chown watcher:watcher /data
USER watcher

# State lives on a volume so restarts don't lose every baseline and re-alert.
VOLUME ["/data"]
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "-m", "pagewatch"]
CMD ["--demo"]
