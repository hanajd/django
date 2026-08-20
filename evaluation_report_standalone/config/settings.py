"""Standalone settings for evaluation report testing."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "standalone-evaluation-report-dev-only-change-me",
)
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core",
    "apps.evaluation_report.apps.EvaluationReportConfig",
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

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        # PDF 编译等长任务期间避免立刻报 database is locked
        "OPTIONS": {
            "timeout": 60,
        },
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "/evaluation-reports/"
LOGOUT_REDIRECT_URL = "/login/"

# ---- evaluation report / LaTeX (aligned with tablet_backend) ----
FILE_LIBRARY_ROOT = MEDIA_ROOT / "file_library"
FILE_LIBRARY_EVALUATION_FORM_DIR = FILE_LIBRARY_ROOT / "evaluation_forms"
FILE_LIBRARY_ATTACHMENT_DIR = FILE_LIBRARY_ROOT / "attachments"

LATEX_TEMPLATE_ROOT = BASE_DIR / "latex"
LATEX_DEFAULT_PROJECT = "yp250420"
LATEX_MAIN_FILE = "main.tex"
LATEX_ENGINE = os.environ.get("LATEX_ENGINE", "lualatex")
LATEX_RUN_TIMES = int(os.environ.get("LATEX_RUN_TIMES", "2"))

CONVERTER_KEYWORDS_CSV = BASE_DIR / "converter" / "data" / "project_keywords.csv"
EVALUATION_REPORT_WORK_ROOT = MEDIA_ROOT / "evaluation_reports" / "work"
EVALUATION_LATEX_TEMPLATE_ROOT = MEDIA_ROOT / "evaluation_reports" / "latex_templates"
EVALUATION_REPORT_ALLOW_INCOMPLETE = os.environ.get(
    "EVALUATION_REPORT_ALLOW_INCOMPLETE", "1"
).lower() in ("1", "true", "yes")
