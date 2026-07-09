# ============================================
# SINGLE SOURCE OF TRUTH - Railway Deployment
# ============================================
# This is the ONLY deployment configuration for Railway.
# 
# Removed conflicting files:
# - backend/railway.toml (was bypassing bulletproof script)
# - backend/nixpacks.toml (not needed with Dockerfile)
# - backend/Dockerfile.lightweight (redundant)
#
# Deployment chain:
# 1. Railway detects this Dockerfile → uses it (highest priority)
# 2. Dockerfile CMD → runs bash railway_start.sh
# 3. railway_start.sh → starts Gunicorn with bulletproof WSGI
# 4. Bulletproof WSGI → always responds (even if Django fails)
# ============================================

FROM python:3.11-slim

WORKDIR /app

# Install minimal system dependencies (soft-coded via ARGs)
ARG INSTALL_OCR=true
ARG INSTALL_PDF=true

RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    libpq-dev \
    gcc \
    python3-dev \
    curl \
    && if [ "$INSTALL_PDF" = "true" ]; then \
         apt-get install -y --no-install-recommends poppler-utils; \
       fi \
    && if [ "$INSTALL_OCR" = "true" ]; then \
         apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng; \
       fi \
    && rm -rf /var/lib/apt/lists/*

# ── Layer 1: Python dependencies (cached unless requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Layer 2: Application code
COPY . .

# Create necessary directories
RUN mkdir -p /app/media /app/staticfiles /app/media/invoices

# Make startup script executable
RUN chmod +x railway_start.sh || true

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# Use JSON array format for CMD to prevent signal issues
# railway_start.sh handles migrations, collectstatic, and starts Gunicorn
# with the bulletproof WSGI (config.wsgi_bulletproof:application)
CMD ["bash", "railway_start.sh"]
