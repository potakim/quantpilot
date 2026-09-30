# QuantPilot 백엔드 이미지 (api·engine·scheduler·migrate가 같은 이미지를 쓴다, docs/07 §2)
# 키·시크릿은 이미지에 넣지 않는다 — 실행 시 env_file로만 주입 (불변식 #10, .dockerignore)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY quantpilot ./quantpilot
RUN pip install ".[data,infra,ai]" \
    && useradd --create-home --uid 10001 qp \
    && mkdir -p /app/data \
    && chown qp:qp /app/data

USER qp
EXPOSE 8000
CMD ["uvicorn", "quantpilot.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
