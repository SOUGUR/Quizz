"""
Django settings for the Quizz (Nexus Platform) project, including the MCQ
extractor app (mcq_app).

All secrets and provider choices come from environment variables (loaded from
`.env` in development). See `.env.example` for every supported variable.
"""

import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env from the project root explicitly so it works no matter which
# directory manage.py / celery is launched from.
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def env_list(name, default=""):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Core / security
# ---------------------------------------------------------------------------
DEBUG = env_bool("DEBUG", False)

SECRET_KEY = os.getenv("SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        # Only acceptable for local development; never used when DEBUG=False.
        SECRET_KEY = "django-insecure-dev-only-key-change-me"
    else:
        raise ImproperlyConfigured(
            "SECRET_KEY must be set when DEBUG=False. For local development copy "
            ".env.example to .env (it sets DEBUG=True and a SECRET_KEY)."
        )

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "127.0.0.1,localhost")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "")

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_HTTPONLY = True

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 3600)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Nexus platform
    "classroom",
    "institute",
    "teacher",
    "tests",
    # MCQ extraction (OCR + LLM)
    "mcq_app",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "Quizz.urls"

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
            ],
        },
    },
]

WSGI_APPLICATION = "Quizz.wsgi.application"

# ---------------------------------------------------------------------------
# Database (SQLite for local development)
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        # Background processing writes while the web thread reads; give SQLite
        # time to wait for the write lock instead of failing immediately.
        "OPTIONS": {"timeout": 20},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.getenv("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

# Authentication: pages behind @login_required redirect here.
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "home"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------
MAX_UPLOAD_SIZE_MB = env_int("MAX_UPLOAD_SIZE_MB", 25)
MAX_PDF_PAGES = env_int("MAX_PDF_PAGES", 100)
# Django buffers anything larger than this to a temp file instead of memory.
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# ---------------------------------------------------------------------------
# OCR pipeline
# ---------------------------------------------------------------------------
MCQ_OCR = {
    # paddle_local | nvidia
    "PROVIDER": os.getenv("OCR_PROVIDER", "paddle_local").strip().lower(),
    # Use the PDF's embedded text layer when a page has one (digital PDFs);
    # OCR is only run on pages without usable text (scanned pages).
    "USE_PDF_TEXT_LAYER": env_bool("OCR_USE_PDF_TEXT_LAYER", True),
    "MIN_TEXT_LAYER_CHARS": env_int("OCR_MIN_TEXT_LAYER_CHARS", 80),
    # pymupdf (pure pip, works everywhere) | poppler (needs poppler binaries)
    "PDF_RENDERER": os.getenv("PDF_RENDERER", "pymupdf").strip().lower(),
    "POPPLER_PATH": os.getenv("POPPLER_PATH", "") or None,
    "DPI": env_int("OCR_DPI", 200),
    "NVIDIA_API_URL": os.getenv("NVIDIA_OCR_API_URL", os.getenv("PADDLE_OCR_API_URL", "")),
    "NVIDIA_API_KEY": os.getenv("NVIDIA_OCR_API_KEY", os.getenv("PADDLE_OCR_API_KEY", "")),
    "TIMEOUT": env_int("OCR_TIMEOUT", 60),
    "RETRIES": env_int("OCR_RETRIES", 2),
}

# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------
MCQ_LLM = {
    # ollama | gemini
    "PROVIDER": os.getenv("LLM_PROVIDER", "ollama").strip().lower(),
    "OLLAMA_BASE_URL": os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
    "OLLAMA_MODEL": os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
    "OLLAMA_NUM_CTX": env_int("OLLAMA_NUM_CTX", 8192),
    "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", ""),
    "GEMINI_MODEL": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    "TIMEOUT": env_int("LLM_TIMEOUT", 300),
    "RETRIES": env_int("LLM_RETRIES", 2),
    "CHUNK_MAX_CHARS": env_int("LLM_CHUNK_MAX_CHARS", 3500),
    # Chunks are cut at question boundaries, so overlap is not needed there
    # (unnumbered text still uses overlapping windows). Set >0 for extra safety.
    "CHUNK_OVERLAP_QUESTIONS": env_int("LLM_CHUNK_OVERLAP_QUESTIONS", 0),
}
# Questions per LLM call (0 = only limited by CHUNK_MAX_CHARS). Measured on a
# 64-page GATE paper with qwen2.5:3b: 1/call -> 94% recall, 2 -> 88%,
# ~7 (3500 chars) -> 56%. Small models skip items in long lists; large cloud
# models do not, so they get big chunks (fewer, cheaper calls).
MCQ_LLM["QUESTIONS_PER_CHUNK"] = env_int(
    "LLM_QUESTIONS_PER_CHUNK", 1 if MCQ_LLM["PROVIDER"] == "ollama" else 0
)

# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------
# thread  -> in-process background thread (default; zero infrastructure)
# celery  -> Celery worker + Redis broker (recommended for production/Linux)
TASK_BACKEND = os.getenv("TASK_BACKEND", "thread").strip().lower()
# A paper stuck in "Processing" longer than this is treated as interrupted.
PROCESSING_STALE_MINUTES = env_int("PROCESSING_STALE_MINUTES", 120)
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s [%(name)s] %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOG_DIR / "mcq_app.log",
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 3,
            "formatter": "standard",
            "encoding": "utf-8",
        },
    },
    "loggers": {
        "mcq_app": {
            "handlers": ["console", "file"],
            "level": os.getenv("LOG_LEVEL", "INFO"),
            "propagate": False,
            # Keep test-suite fake data out of the real log file.
            **({"handlers": [], "level": "CRITICAL"} if "test" in sys.argv[1:2] else {}),
        },
    },
}
