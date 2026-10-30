"""고티처 설정. 값은 모두 환경변수에서 읽는다(.env.example 참고)."""
import os
from pathlib import Path
from urllib.parse import urlparse

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent  # app/
REPO_DIR = BASE_DIR.parent

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
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
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

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [REPO_DIR / "web" / "static"]
STATIC_ROOT = REPO_DIR / "staticfiles"

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
