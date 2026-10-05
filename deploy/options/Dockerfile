FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-scout.txt ./
RUN pip install --no-cache-dir -r requirements-scout.txt && useradd --uid 10001 --create-home scout && mkdir /data && chown scout:scout /data
COPY scout_options ./scout_options
COPY options.html ./options.html
USER scout
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 CMD ["python", "-m", "scout_options.health", "/data/options-status.json"]
CMD ["python", "-m", "scout_options.service", "--capital", "10000", "--ledger", "/data/ledger.sqlite", "--status", "/data/options-status.json"]
