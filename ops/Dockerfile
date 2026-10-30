# 고티처 앱 이미지. 빌드 컨텍스트는 저장소 루트(ops/compose.yml의 context: ..).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

WORKDIR /srv
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY web ./web
RUN SECRET_KEY=build-only python app/manage.py collectstatic --noinput -v 0

RUN useradd --system --no-create-home gt
USER gt

EXPOSE 8000
CMD ["gunicorn", "config.wsgi", "--chdir", "app", "--bind", "0.0.0.0:8000", "--workers", "2"]
