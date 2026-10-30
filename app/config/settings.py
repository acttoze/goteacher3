"""고티처 설정. 값은 모두 환경변수에서 읽는다(.env.example 참고)."""
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent  # app/
REPO_DIR = BASE_DIR.parent
if str(REPO_DIR) not in sys.path:  # 저장소 루트의 hwp 패키지(Django 없는 한글 엔진)를 import 하려고
    sys.path.append(str(REPO_DIR))

# 로컬 편의: 저장소 루트 .env가 있으면 읽는다(이미 설정된 환경변수가 우선).
_env_file = REPO_DIR / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).lower() in ("1", "true", "yes", "on")


DEBUG = env_bool("DEBUG")
SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    raise ImproperlyConfigured("SECRET_KEY 환경변수가 필요합니다(.env.example 참고).")

SITE_URL = os.environ.get("SITE_URL", "http://localhost:8000").rstrip("/")
_site_host = urlparse(SITE_URL).hostname
ALLOWED_HOSTS = [h for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h]
if _site_host and _site_host not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_site_host)
if DEBUG:
    ALLOWED_HOSTS += ["localhost", "127.0.0.1"]
# 구글 로그인. 리디렉션 주소는 SITE_URL + "/auth/google/callback"
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
# NEIS 오픈API 대표 인증키. 비우면 키 없이 부른다(한 번에 5건 제한).
NEIS_KEY = os.environ.get("NEIS_KEY", "")
# 작업 큐(config/queue.py). 비우면(로컬·CI) 큐 없이 그 자리에서 실행한다.
REDIS_URL = os.environ.get("REDIS_URL", "")
RQ_JOB_TIMEOUT = 300  # 작업당 상한(초). 한글 변환 표본 최대 205초(30번 리드 결정)

CSRF_TRUSTED_ORIGINS = [SITE_URL] if SITE_URL.startswith("https://") else []

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "spaces",
    "collect",
    "hwpfiles",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # DEBUG=0에서 /static/ 서빙(Traefik 뒤 gunicorn)
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.DeviceTokenMiddleware",
    "spaces.middleware.SpaceMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "collect.bell.unread",
            ],
        },
    },
]

# 운영은 PostgreSQL 17(DATABASE_URL=postgres://...). 값이 없으면 로컬 sqlite.
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}

AUTH_PASSWORD_VALIDATORS = []
LOGIN_URL = "/start"  # 시작 화면(구글로 시작하기). / 는 로그인 전 소개 페이지

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [REPO_DIR / "web" / "static"]
STATIC_ROOT = REPO_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# 올린 한글 파일(E2). 서버는 볼륨 /srv/media(ops/compose.yml). MEDIA_URL로 서빙하지 않는다(내려받기는 회원 확인 뷰로만).
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", str(REPO_DIR / "media"))
FILE_UPLOAD_PERMISSIONS = 0o600
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o700
# 한글 파일 하나 상한(hwp.MAX_UPLOAD와 같음). 파일이 아닌 본문은 Django 기본(2.5MB) 그대로,
# 파일은 2.5MB를 넘으면 임시 파일로 받아 메모리에 다 올리지 않는다. 선언 크기 초과는 뷰가 본문 읽기 전에 거부.
HWP_MAX_UPLOAD = 30 * 1024 * 1024
HWP_IN_FLIGHT = 2  # 회원 한 명이 동시에 맡길 수 있는 변환(대기·변환 중)
HWP_SPACE_QUOTA = 1024 * 1024 * 1024  # 공간 하나의 저장 총량(원본+변환한 HWPX)
HWP_TOTAL_QUOTA = int(os.environ.get("HWP_TOTAL_QUOTA", 20 * 1024**3))  # 서비스 전체(서버 디스크 보호)
FILE_UPLOAD_MAX_MEMORY_SIZE = 2_621_440
DATA_UPLOAD_MAX_MEMORY_SIZE = 2_621_440

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 세션·기기 쿠키: 스크립트에서 못 읽고, 구글에서 돌아오는 최상위 GET(콜백)에는 실려야 하므로 Lax
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# Traefik 뒤에서 HTTPS 판별
if not DEBUG:
    SECURE_HSTS_SECONDS = 3600  # 짧게 시작, 안정되면 늘린다
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
