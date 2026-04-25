FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV TZ=Asia/Singapore

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git tzdata ripgrep \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md .env.example ./
COPY app ./app
COPY tests ./tests

RUN pip install --no-cache-dir -e .

CMD ["python", "-m", "app.cli", "bot"]
