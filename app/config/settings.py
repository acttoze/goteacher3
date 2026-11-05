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
# 웹 푸시(G2). 비우면 구독 단추를 숨기고 보내지 않는다. 키 만드는 법은 ops/README.md
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@goteacher.xyz")
# 학교 플랜 견적서(O1). 비우면 그 칸에 "담당자가 안내드려요". 가격은 숫자만(1년 기준)
SCHOOL_PLAN_PRICE = os.environ.get("SCHOOL_PLAN_PRICE", "").strip()
SUPPLIER_NAME = os.environ.get("SUPPLIER_NAME", "").strip()
SUPPLIER_BIZ_NO = os.environ.get("SUPPLIER_BIZ_NO", "").strip()
SUPPLIER_ADDRESS = os.environ.get("SUPPLIER_ADDRESS", "").strip()
SUPPLIER_ACCOUNT = os.environ.get("SUPPLIER_ACCOUNT", "").strip()
SUPPLIER_CEO = os.environ.get("SUPPLIER_CEO", "").strip()
SCHOOL_PLAN_VAT = os.environ.get("SCHOOL_PLAN_VAT", "").strip()  # 견적서 부가세 칸에 그대로 적는 글자(금액 또는 처리 방식)
SCHOOL_PLAN_TOTAL = os.environ.get("SCHOOL_PLAN_TOTAL", "").strip()  # 합계 금액(숫자)
QUOTE_VALID_DAYS = os.environ.get("QUOTE_VALID_DAYS", "").strip()  # 발행일부터 며칠(숫자)
RQ_JOB_TIMEOUT = 300  # 작업당 상한(초). 한글 변환 표본 최대 205초(30번 리드 결정)

# 빈도 제한(config/ratelimit.py): 여러 gunicorn 프로세스가 같은 수를 세도록 큐와 같은 Redis의 다른 DB(/1). Redis가 멈춰도 요청이 붙잡히지 않게 1초에서 끊는다.
# NEIS 응답 캐시(default)는 프로세스 메모리 그대로 — Redis가 noeviction 96MB라 큐와 메모리를 다투지 않게.
_RL = ({"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": urlparse(REDIS_URL)._replace(path="/1").geturl(),
        "OPTIONS": {"socket_connect_timeout": 1, "socket_timeout": 1}} if REDIS_URL
       else {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "ratelimit"})
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}, "ratelimit": _RL}

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
    "today",
    "library",
    "notices",
    "repairs",
    "meals",
    "partners",
    "community",
]

MIDDLEWARE = [
    "config.bodylimit.BodyLimitMiddleware",  # 본문을 읽는 미들웨어(CSRF)보다 앞에서 선언 크기를 본다
    "django.middleware.security.SecurityMiddleware",
    "config.csp.CspMiddleware",  # 모든 응답 공통 CSP(화면이 제 것을 붙였으면 그대로)
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
                "spaces.context.space_word",
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
# 한글 파일 하나 상한(hwp.MAX_UPLOAD와 같음). 파일은 2.5MB를 넘으면 임시 파일로 받아 메모리에 다 올리지 않는다.
HWP_MAX_UPLOAD = 30 * 1024 * 1024
# 요청 본문 상한(config.bodylimit). 앞단 Traefik 라우터(ops/compose.yml)와 같은 값·같은 주소를 쓴다.
MAX_REQUEST_BYTES = 3 * 1024 * 1024  # 일반 POST. 붙여넣은 표 HTML 100만 자(인코딩 뒤 1.9MB)도 든다
MAX_NOTICE_REQUEST_BYTES = 101 * 1024 * 1024  # 공지 쓰기·고치기: 첨부 100MB + 머리 몫
MAX_HWP_REQUEST_BYTES = HWP_MAX_UPLOAD + 1024 * 1024  # 한글 올리기
MAX_PHOTO_REQUEST_BYTES = 9 * 1024 * 1024  # 수리·식판·폰 사진: 8MB + 머리 몫
HWP_IN_FLIGHT = 2  # 회원 한 명이 동시에 맡길 수 있는 변환(대기·변환 중)
HWP_SPACE_QUOTA = 500 * 1024 * 1024  # 동학년 공간 하나의 저장 총량(원본+변환한 HWPX)
HWP_FREE_TOTAL_QUOTA = int(os.environ.get("HWP_FREE_TOTAL_QUOTA", 2 * 1024**3))  # 동학년 공간 합계. 무료 공간이 유료 학교 자리까지 쓰지 못하게(hwpfiles.models.storage_room)
HWP_SCHOOL_QUOTA = int(os.environ.get("HWP_SCHOOL_QUOTA", 2 * 1024**3))  # 학교 공간 하나(H4). 서비스 전체 한도가 디스크를 지킨다
HWP_TOTAL_QUOTA = int(os.environ.get("HWP_TOTAL_QUOTA", 5 * 1024**3))  # 서비스 전체(서버 디스크 보호). 미디어 백업 7벌을 감안(한글·공지 첨부·수리·식판 사진 합, SourceFile.used_bytes)
PARTNER_REVIEW_MIN_DAYS = 14  # N2 후기 자격: 무료 공간은 가입 뒤 이 날수가 지나야
PARTNER_REVIEW_MIN_GOOGLE = 2  # N2 후기 자격: 무료 공간은 구글 회원이 이만큼 있어야
PARTNER_REVIEW_PER_DAY = 5  # N2 후기 제출 한도(사용자당 하루). 거부된 제출도 센다(명부 확인 통로 차단)
FILE_UPLOAD_MAX_MEMORY_SIZE = 2_621_440
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_REQUEST_BYTES  # 같은 값이라 2.5~3MB에서 Django 400이 따로 나지 않는다(넘으면 413 하나)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 세션·기기 쿠키: 스크립트에서 못 읽고, 구글에서 돌아오는 최상위 GET(콜백)에는 실려야 하므로 Lax
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# Traefik 뒤에서 HTTPS 판별
if not DEBUG:
    # 스테이징은 짧게(기본 3600). 운영 도메인(G7)에서 31536000. 서브도메인 포함·preload는 쓰지 않는다(goteacher.xyz 구버전 보호)
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", 3600))
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# 500 오류는 docker logs에 경로(토큰 가림)·예외 종류·코드 위치만 남긴다. 값이 들어갈 수 있는 것은 빼고(config/logsafe.py).
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"safe": {"()": "config.logsafe.SafeErrorFormatter"}},
    "handlers": {"stderr": {"class": "logging.StreamHandler", "formatter": "safe", "level": "ERROR"}},
    "loggers": {"django.request": {"handlers": ["stderr"], "level": "ERROR", "propagate": False}},
}
