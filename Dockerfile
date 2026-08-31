FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    TENURE_DATA_DIR=/tmp/tenure/runs

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir ".[agent,api,cloud]"

EXPOSE 8080

CMD ["sh", "-c", "uvicorn tenure.api:app --host 0.0.0.0 --port ${PORT}"]
