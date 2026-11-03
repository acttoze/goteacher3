# 고티처 앱 이미지. 빌드 컨텍스트는 저장소 루트(ops/compose.yml의 context: ..).
# 기반 이미지는 다이제스트로 고정(갱신 방법: ops/README.md). 2026-11-03 서버 빌드에 쓰인 것과 같은 값
FROM python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30

COPY --from=ghcr.io/astral-sh/uv:0.10.8@sha256:88234bc9e09c2b2f6d176a3daf411419eb0370d450a08129257410de9cfafd2a /uv /usr/local/bin/uv

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
COPY hwp ./hwp
COPY web ./web
RUN SECRET_KEY=build-only python app/manage.py collectstatic --noinput -v 0 \
    && chmod -R a+rX /srv/staticfiles  # 업로드 폴더 권한(700) 설정이 collectstatic에도 걸려 gt가 못 읽는 것 방지

RUN useradd --system --no-create-home gt \
    && mkdir -p /srv/media && chown gt /srv/media && chmod 700 /srv/media  # 올린 한글 파일 볼륨(새 볼륨이 이 소유권을 받음)
ENV MEDIA_ROOT=/srv/media
USER gt

EXPOSE 8000
CMD ["gunicorn", "config.wsgi", "--chdir", "app", "--bind", "0.0.0.0:8000", "--workers", "2"]
