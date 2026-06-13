FROM python:3.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Tokyo

COPY docker/requirements.txt ./requirements.txt
COPY docker/requirements-collector.txt ./requirements-collector.txt
RUN pip install --no-cache-dir -r requirements.txt -r requirements-collector.txt \
    && playwright install --with-deps chromium

COPY src ./src

CMD ["python", "-m", "src.collector.main"]
