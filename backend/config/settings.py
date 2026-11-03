"""
Django settings for ERP project.
"""

import sys
from datetime import timedelta
from pathlib import Path
import environ
from django.core.exceptions import ImproperlyConfigured
from corsheaders.defaults import default_headers as default_cors_headers

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Fail closed: DEBUG is off unless the environment turns it on (.env.example
# does, for local development).
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CORS_ALLOWED_ORIGINS=(list, ["http://localhost:5173", "http://127.0.0.1:5173"]),
)

# Read .env file if present
environ.Env.read_env(BASE_DIR / ".env")

DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# A known or short SECRET_KEY lets anyone forge sessions and JWTs. Refuse to
# start with one unless this is local development (DEBUG) or the test suite.
_RUNNING_TESTS = "pytest" in sys.modules or sys.argv[1:2] == ["test"]
_KNOWN_INSECURE_KEYS = {
    "",
    "django-insecure-default-key-change-me",
    "django-insecure-change-me-in-production-1234567890",
    "django-insecure-docker-secret-key-12345",
    "REPLACE_WITH_A_FRESHLY_GENERATED_SECRET",
}
SECRET_KEY = env("SECRET_KEY", default="")
if SECRET_KEY in _KNOWN_INSECURE_KEYS or len(SECRET_KEY) < 32:
    if not (DEBUG or _RUNNING_TESTS):
        raise ImproperlyConfigured(
            "SECRET_KEY is missing, a published example value, or shorter than 32 "
            'characters. Generate one with: python -c "import secrets; '
            'print(secrets.token_urlsafe(50))" and set it in backend/.env. '
            "For local development set DEBUG=True instead (see .env.example)."
        )
    SECRET_KEY = SECRET_KEY or "django-insecure-local-development-only-key-0000"

# Django admin path. Moving it off /admin/ cuts automated password guessing;
# set ADMIN_URL (e.g. "office-7f3a/") on the server and in the nginx config.
ADMIN_URL = env("ADMIN_URL", default="admin/")

INSTALLED_APPS = [
    # Django core apps
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party apps
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
    # Local apps
    "core.apps.CoreConfig",
    "users.apps.UsersConfig",
    "contacts.apps.ContactsConfig",
    "accounts.apps.AccountsConfig",
    "stock.apps.StockConfig",
    "selling.apps.SellingConfig",
    "buying.apps.BuyingConfig",
    "gst.apps.GstConfig",
    "manufacturing.apps.ManufacturingConfig",
    "hr.apps.HrConfig",
    "drive.apps.DriveConfig",
    "ngu.apps.NguConfig",
    "tally_export.apps.TallyExportConfig",
    "payment_reconciliation.apps.PaymentReconciliationConfig",
    "assistant.apps.AssistantConfig",
    "notifications",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Database
# Default to SQLite for ease of setup/testing, or use DATABASE_URL if PostgreSQL is configured
DATABASES = {
    "default": env.db_url(
        "DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
    )
}

# Custom User Model
AUTH_USER_MODEL = "users.User"

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# i18n & L10n settings (Plan §1.3: 13 languages)
LANGUAGE_CODE = "en-us"

LANGUAGES = [
    ("en", "English"),
    ("hi", "Hindi"),
    ("ta", "Tamil"),
    ("pa", "Punjabi"),
    ("gu", "Gujarati"),
    ("or", "Odia"),
    ("bn", "Bengali"),
    ("kn", "Kannada"),
    ("as", "Assamese"),
    ("te", "Telugu"),
    ("ml", "Malayalam"),
    ("ur", "Urdu"),
    ("mr", "Marathi"),
]

LOCALE_PATHS = [
    BASE_DIR / "locale",
]

TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Uploaded proof documents (core.Attachment). Files live under MEDIA_ROOT but are
# NEVER served from MEDIA_URL: downloads go through the authenticated, RBAC-checked
# /api/core/attachments/<id>/download/ endpoint.
MEDIA_URL = "media/"
MEDIA_ROOT = Path(env.str("MEDIA_ROOT", default=str(BASE_DIR / "media")))
ATTACHMENT_MAX_BYTES = env.int("ATTACHMENT_MAX_BYTES", default=10 * 1024 * 1024)

# Organisation drive files are also private: authenticated download endpoints only.
DRIVE_MAX_BYTES = env.int("DRIVE_MAX_BYTES", default=25 * 1024 * 1024)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Cache: rate limits (login brute-force guard) live here, so every gunicorn
# worker must share it. A per-process memory cache would give each of the 3
# workers its own counter. Production defaults to a file cache on this server;
# set CACHE_URL (e.g. redis://...) to use a shared cache service instead.
CACHES = {
    "default": env.cache_url(
        "CACHE_URL",
        default=(
            "locmemcache://"
            if DEBUG or _RUNNING_TESTS
            else f"filecache://{BASE_DIR / 'var' / 'cache'}"
        ),
    )
}

# CORS configuration
# Local development only: allow any origin so the SPA works from any port or LAN
# IP (Vite hops to 5174+ when 5173 is taken). Production stays on the explicit
# allow-list from the environment.
CORS_ALLOW_ALL_ORIGINS = DEBUG
CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")
# The SPA sends the active company on every request (core.company_context).
CORS_ALLOW_HEADERS = (*default_cors_headers, "x-company-id", "x-assistant-proposal")

# Django REST Framework configuration
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "users.auth.SessionAwareJWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    # Rate limiting. Anonymous traffic is capped hard; authenticated ERP users
    # get a generous ceiling (bulk data entry is legitimate). The `auth` scope is
    # a brute-force guard applied only to the JWT token endpoint (see
    # users.views.ThrottledTokenObtainPairView).
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.ScopedRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("THROTTLE_ANON", default="30/min"),
        "user": env("THROTTLE_USER", default="1000/min"),
        "auth": env("THROTTLE_AUTH", default="10/min"),
        # Employee portal login / change-PIN (hr.portal_views), per IP.
        "portal_login": env("THROTTLE_PORTAL_LOGIN", default="10/min"),
        "assistant": env("THROTTLE_ASSISTANT", default="60/min"),
        "assistant_day": env("THROTTLE_ASSISTANT_DAY", default="1000/day"),
        "assistant_stt": env("THROTTLE_ASSISTANT_STT", default="30/min"),
        "assistant_bill": env("THROTTLE_ASSISTANT_BILL", default="60/hour"),
    },
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": "core.pagination.StablePageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

# SimpleJWT settings
# Short-lived access tokens (the SPA refreshes silently on a 401); a refresh
# token is single-use (blacklisted on rotation) and revocable (users.auth).
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=env.int("JWT_ACCESS_MINUTES", default=60)
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=7)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# drf-spectacular OpenAPI settings
SPECTACULAR_SETTINGS = {
    "TITLE": "ERP API",
    "DESCRIPTION": "Custom ERP system reproducing ERPNext logic on Django DRF + React",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # The API map lists every route and field; only signed-in users (e.g. via
    # the admin session) may read it outside local development.
    "SERVE_PERMISSIONS": [
        (
            "rest_framework.permissions.AllowAny"
            if DEBUG
            else "rest_framework.permissions.IsAuthenticated"
        )
    ],
}

# Access & role defaults
CREDIT_CONTROLLER_ROLE = env("CREDIT_CONTROLLER_ROLE", default="Staff")

# Email and notification settings
EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = env("EMAIL_HOST", default="smtp.gmail.com")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL", default=EMAIL_HOST_USER or "webmaster@localhost"
)
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=10)
ADMIN_ALERT_EMAIL = env("ADMIN_ALERT_EMAIL", default=EMAIL_HOST_USER)
FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:5173")

# Nightly database backup (core.backup_db, run by run_scheduler at 01:30).
BACKUP_ENABLED = env.bool("BACKUP_ENABLED", default=True)
BACKUP_DIR = Path(env.str("BACKUP_DIR", default=str(BASE_DIR / "backups")))
BACKUP_RETENTION_DAYS = env.int("BACKUP_RETENTION_DAYS", default=30)
# rclone destination for the off-site copy, e.g. "gdrive:erp-backups". Empty = off.
BACKUP_RCLONE_REMOTE = env.str("BACKUP_RCLONE_REMOTE", default="")

# ── Production transport security (behind nginx + HTTPS, see DEPLOYMENT.md) ──
if not DEBUG:
    # nginx terminates TLS and always sets X-Forwarded-Proto (DEPLOYMENT.md);
    # never enable this without a proxy that overwrites the header.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)
    # Set to False only for a plain-HTTP LAN install (admin login needs it).
    SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=True)
    CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=True)
    # Browsers ignore HSTS over plain HTTP, so this is safe on a LAN install too.
    SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 30)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
        "SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False
    )
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
