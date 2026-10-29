"""
Django settings for ERP project.
"""

from datetime import timedelta
from pathlib import Path
import environ
from corsheaders.defaults import default_headers as default_cors_headers

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, True),
    SECRET_KEY=(str, "django-insecure-default-key-change-me"),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CORS_ALLOWED_ORIGINS=(list, ["http://localhost:5173", "http://127.0.0.1:5173"]),
)

# Read .env file if present
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

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
    "legacy.apps.LegacyConfig",
    "ngu.apps.NguConfig",
    "tally_export.apps.TallyExportConfig",
    "payment_reconciliation.apps.PaymentReconciliationConfig",
    "assistant.apps.AssistantConfig",
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
    "default": env.db_url("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
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
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
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
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=8),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# drf-spectacular OpenAPI settings
SPECTACULAR_SETTINGS = {
    "TITLE": "ERP API",
    "DESCRIPTION": "Custom ERP system reproducing ERPNext logic on Django DRF + React",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}
