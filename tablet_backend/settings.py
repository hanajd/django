"""
Django 项目配置文件
"""
import os
from pathlib import Path
from datetime import timedelta

# 构建路径
BASE_DIR = Path(__file__).resolve().parent.parent

# 安全密钥（生产环境应使用环境变量）
SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-change-this-in-production')

# 调试模式
DEBUG = True

# 允许的主机
ALLOWED_HOSTS = ['*']

# 应用定义
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # 第三方应用
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'django_filters',
    
    # 自定义应用
    'apps.core',
    'apps.api',
]

# 中间件
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',  # CORS 中间件
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'apps.core.middleware.WebSessionLeaseMiddleware',
    'apps.core.middleware.PartyADemoSecurityMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'tablet_backend.urls'

# 模板配置
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.core.context_processors.menu_context',  # 动态菜单上下文处理器
            ],
        },
    },
]

# WSGI 配置
WSGI_APPLICATION = 'tablet_backend.wsgi.application'

# 数据库配置（使用 SQLite，保持扩展性）
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
    # 如需使用 MySQL/PostgreSQL，可取消注释以下配置并修改相应参数
    # 'default': {
    #     'ENGINE': 'django.db.backends.mysql',
    #     'NAME': 'tablet_backend',
    #     'USER': 'root',
    #     'PASSWORD': 'password',
    #     'HOST': 'localhost',
    #     'PORT': '3306',
    # }
}

# 密码验证
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# 国际化配置
LANGUAGE_CODE = 'zh-hans'  # 简体中文
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True

# 静态文件配置
STATIC_URL = '/static/'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]
STATIC_ROOT = BASE_DIR / 'staticfiles'

# 媒体文件配置
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# File library + pipeline workspace under MEDIA_ROOT
FILE_LIBRARY_ROOT = MEDIA_ROOT / 'file_library'
FILE_LIBRARY_UPLOAD_DIR = FILE_LIBRARY_ROOT / 'uploads'
FILE_LIBRARY_JSON_DIR = FILE_LIBRARY_ROOT / 'json'
FILE_LIBRARY_TEMPLATE_DIR = FILE_LIBRARY_ROOT / 'templates'
FILE_LIBRARY_SITE_RECORD_DIR = FILE_LIBRARY_ROOT / 'site_records'
FILE_LIBRARY_REPORT_DIR = FILE_LIBRARY_ROOT / 'reports'
FILE_LIBRARY_ATTACHMENT_DIR = FILE_LIBRARY_ROOT / 'attachments'
FILE_LIBRARY_INSPECTION_SUBMIT_DIR = FILE_LIBRARY_ROOT / 'inspection_submits'
FILE_LIBRARY_TEMP_ROOT = FILE_LIBRARY_ROOT / 'temp'
# Paths passed to utils.pipeline_config.set_pipeline_directories（见 apps.core.pipeline_service）
PIPELINE_TEMP_PDF = FILE_LIBRARY_TEMP_ROOT / 'temp_pdf'
PIPELINE_BATCH_ARCHIVE = FILE_LIBRARY_TEMP_ROOT / 'batches'
PIPELINE_PREVIEW_IMG = FILE_LIBRARY_TEMP_ROOT / 'preview_images'
PIPELINE_STATIC = FILE_LIBRARY_TEMP_ROOT / 'static'
PIPELINE_MINERU_MD = FILE_LIBRARY_TEMP_ROOT / 'mineru_md'

# MinerU / Ollama：管线启动前由 apps.core.pipeline_service 同步到 os.environ
# MINERU_BACKEND：未在环境中设置时 Django 默认 pipeline（避免 hybrid+vLLM 依赖 Triton/gcc 失败）。
# 需要 MinerU2.5 hybrid 时在 shell 中 export MINERU_BACKEND=hybrid-auto-engine 后再启动。
MINERU_BACKEND = os.environ.get('MINERU_BACKEND', 'pipeline')
# OLLAMA_OPTIONS 为 JSON，例如 '{"num_gpu":999,"num_ctx":8192}'；不设则默认尽量用 GPU。
# 多卡与动态显存：见 utils.gpu_scheduler（PIPELINE_GPU_AUTO_MINERU / PIPELINE_GPU_AUTO_OLLAMA、
# MINERU_CUDA_VISIBLE_DEVICES、OLLAMA_PREFERRED_GPU_INDEX、PIPELINE_OLLAMA_NUM_CTX_* 等）。
OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://127.0.0.1:11434')

# 默认主键类型
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# 运行端口配置（通过环境变量）
PORT = os.environ.get('DJANGO_PORT', '11223')

# Django REST Framework 配置
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',  # Web 端使用 Session 认证
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_FILTER_BACKENDS': (
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ),
}

# JWT 认证配置
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=2),  # 访问令牌有效期 2 小时
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),  # 刷新令牌有效期 7 天
    'ROTATE_REFRESH_TOKENS': True,  # 刷新时轮换刷新令牌
    'BLACKLIST_AFTER_ROTATION': True,  # 轮换后将旧令牌加入黑名单
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_TOKEN_CLASSES': ('rest_framework_simplejwt.tokens.AccessToken',),
}

# CORS 配置（允许跨域访问）
CORS_ALLOWED_ORIGINS = [
    "http://localhost:11223",
    "http://127.0.0.1:11223",
]
CORS_ALLOW_CREDENTIALS = True

# 登录重定向 URL
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# 外网暴露演示账号 test（旧名 party_a_demo）或 perm_overrides.party_a_demo_restrictions 时的可选加固
# PARTY_A_DEMO_ALLOWED_IPS：逗号分隔；支持单 IP 或 CIDR（如 203.0.113.0/24）。留空则不限制来源。
PARTY_A_DEMO_ALLOWED_IPS = os.environ.get("PARTY_A_DEMO_ALLOWED_IPS", "").strip()
# 仅在反向代理正确设置且仅可信客户端可达时设为 true，否则易被伪造 X-Forwarded-For
PARTY_A_DEMO_TRUST_X_FORWARDED_FOR = (
    os.environ.get("PARTY_A_DEMO_TRUST_X_FORWARDED_FOR", "").lower() in ("1", "true", "yes")
)
# 为 true 时演示账号无法使用「OCR 处理管线」页面（减轻 GPU/子进程负载）
PARTY_A_DEMO_DISABLE_PIPELINE = (
    os.environ.get("PARTY_A_DEMO_DISABLE_PIPELINE", "").lower() in ("1", "true", "yes")
)

# 同一账号：Web（Session）仅保留最后一次浏览器登录；平板（JWT 密码登录）仅保留最后一次 refresh 链。二者互不注销。
# 设为 0/false/no/off 可关闭（例如内网调试多人共用后台账号）。
AUTH_SINGLE_WEB_SESSION_PER_USER = os.environ.get(
    "AUTH_SINGLE_WEB_SESSION_PER_USER", "1"
).strip().lower() not in ("0", "false", "no", "off")
# 为 true 时 Django 超级用户不受「单 Web 会话」限制（仍受平板 JWT 吊销策略影响，除非关闭下一项）。
AUTH_SINGLE_WEB_SESSION_SKIP_SUPERUSER = os.environ.get(
    "AUTH_SINGLE_WEB_SESSION_SKIP_SUPERUSER", ""
).strip().lower() in ("1", "true", "yes")
# 平板 /api 使用账号密码登录换 JWT 时，吊销该用户此前签发的 refresh（需安装 token_blacklist）。
AUTH_REVOKE_PRIOR_REFRESH_TOKENS_ON_LOGIN = os.environ.get(
    "AUTH_REVOKE_PRIOR_REFRESH_TOKENS_ON_LOGIN", "1"
).strip().lower() not in ("0", "false", "no", "off")
