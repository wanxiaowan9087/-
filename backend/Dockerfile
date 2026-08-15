FROM python:3.11.8-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app

COPY pyproject.toml requirements.lock ./
RUN pip install --index-url https://mirrors.aliyun.com/pypi/simple \
    --timeout 120 \
    --retries 5 \
    --require-hashes \
    --no-deps \
    -r requirements.lock

COPY backend ./backend
COPY data ./data
USER app
EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

