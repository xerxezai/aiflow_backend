#!/bin/bash
# Railway Production Start Script - BULLETPROOF VERSION
# This script will ALWAYS start Gunicorn, even if optional components fail
# ONLY critical failure: Python/Gunicorn not installed

# DO NOT exit on errors - handle them gracefully
set +e  # Continue on errors
set -o pipefail  # Catch pipeline errors

# Activate virtual environment if it exists
if [ -f "/opt/venv/bin/activate" ]; then
    source /opt/venv/bin/activate || true
fi

export DJANGO_SETTINGS_MODULE=config.settings
export PYTHONUNBUFFERED=1
export PORT="${PORT:-8000}"

echo "========================================"
echo "🚀 Railway Deployment (Bulletproof Mode)"
echo "========================================"
echo "Environment : ${RAILWAY_ENVIRONMENT:-production}"
echo "PORT        : ${PORT}"
echo "Python      : $(python --version 2>&1 || echo 'Unknown')"
echo "========================================"
echo ""

# SOFT-CODED: All checks are optional - deployment continues regardless
DEPLOYMENT_WARNINGS=0

# Check 1: Environment Variables (OPTIONAL - warn only)
echo "📋 Checking Environment Variables..."
if [ -f "validate_railway_env.py" ]; then
    python validate_railway_env.py 2>&1 || DEPLOYMENT_WARNINGS=$((DEPLOYMENT_WARNINGS + 1))
fi
echo ""

# Check 2: Django Configuration (OPTIONAL - warn only)
echo "🔍 Testing Django Configuration..."
if python -c "import django; django.setup(); print('✅ Django loaded successfully')" 2>&1; then
    echo "✅ Django configuration valid"
else
    echo "⚠️  WARNING: Django configuration has issues (continuing anyway)"
    echo "   Server will start but may have runtime errors"
    DEPLOYMENT_WARNINGS=$((DEPLOYMENT_WARNINGS + 1))
fi
echo ""
# Check 3: Static Files Collection (OPTIONAL - warn only)
echo "📦 Collecting Static Files..."
if python manage.py collectstatic --noinput --clear 2>&1; then
    echo "✅ Static files collected"
else
    echo "⚠️  WARNING: Static files collection failed (non-critical)"
    DEPLOYMENT_WARNINGS=$((DEPLOYMENT_WARNINGS + 1))
fi
echo ""

# Check 4: Database Migrations (OPTIONAL - warn only)
echo "🗄️  Database Migrations..."

# Migration conflict fixers (if they exist)
[ -f "fix_migration_record.py" ] && python fix_migration_record.py 2>&1 || true
[ -f "fix_migration_conflict.py" ] && python fix_migration_conflict.py 2>&1 || true

# Run migrations - continue even if they fail
if python manage.py migrate --noinput 2>&1; then
    echo "✅ Database migrations completed"
else
    echo "⚠️  WARNING: Database migrations failed"
    echo "   This may be normal on first deploy or if DATABASE_URL not set"
    echo "   Server will start but database operations may fail"
    DEPLOYMENT_WARNINGS=$((DEPLOYMENT_WARNINGS + 1))
fi
echo ""

# Check 5: Super Admin Setup (OPTIONAL)
if [ -f "setup_superadmin.py" ]; then
    echo "👤 Setting up Super Administrator..."
    python manage.py shell < setup_superadmin.py 2>&1 || true
fi
echo ""

# Check 6: Celery Worker (OPTIONAL - disabled by default)
if [ "${CELERY_WORKER_ENABLED:-false}" = "true" ]; then
    echo "🔧 Starting Celery Worker (background)..."    
    celery -A config worker \
        --loglevel=info \
        --concurrency="${CELERY_CONCURRENCY:-1}" \
        --pool=prefork \
        --max-tasks-per-child="${CELERY_MAX_TASKS_PER_CHILD:-100}" \
        --without-heartbeat \
        --without-mingle \
        2>&1 | stdbuf -oL sed 's/^/[Celery] /' &
    echo "✅ Celery worker started"
else
    echo "ℹ️  Celery disabled (set CELERY_WORKER_ENABLED=true to enable)"
fi
echo ""

# ============================================
# DEPLOYMENT SUMMARY
# ============================================
echo "========================================"
echo "📊 Pre-Flight Summary"
echo "========================================"
if [ $DEPLOYMENT_WARNINGS -eq 0 ]; then
    echo "✅ All checks passed - no warnings"
else
    echo "⚠️  $DEPLOYMENT_WARNINGS warning(s) detected"
    echo "   Server will start but some features may not work"
    echo "   Check logs above for details"
fi
echo ""
echo "🚀 STARTING WEB SERVER (Gunicorn)"
echo "========================================"
echo "Bind Address : 0.0.0.0:${PORT}"
echo "Workers      : ${GUNICORN_WORKERS:-2}"
echo "Threads      : ${GUNICORN_THREADS:-4}"
echo "Worker Class : ${GUNICORN_WORKER_CLASS:-gthread}"
echo "Timeout      : ${GUNICORN_TIMEOUT:-120}s"
echo "========================================"
echo ""

# ============================================
# START GUNICORN (ALWAYS RUNS)
# ============================================
# SOFT-CODED Gunicorn Configuration:
#   GUNICORN_WORKERS (default: 2) - worker processes
#   GUNICORN_THREADS (default: 4) - threads per worker  
#   GUNICORN_WORKER_CLASS (default: gthread) - worker type
#   GUNICORN_TIMEOUT (default: 120) - request timeout seconds
#   GUNICORN_KEEPALIVE (default: 75) - TCP keep-alive seconds

# Use exec to replace shell with Gunicorn (proper signal handling)
# BULLETPROOF: Use wsgi_bulletproof which ALWAYS responds (even if Django fails)
exec gunicorn config.wsgi_bulletproof:application \
    --bind "0.0.0.0:${PORT}" \
    --workers "${GUNICORN_WORKERS:-2}" \
    --threads "${GUNICORN_THREADS:-4}" \
    --worker-class "${GUNICORN_WORKER_CLASS:-gthread}" \
    --timeout "${GUNICORN_TIMEOUT:-120}" \
    --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}" \
    --keep-alive "${GUNICORN_KEEPALIVE:-75}" \
    --max-requests "${GUNICORN_MAX_REQUESTS:-500}" \
    --max-requests-jitter "${GUNICORN_MAX_REQUESTS_JITTER:-50}" \
    --log-file - \
    --access-logfile - \
    --error-logfile - \
    --log-level "${GUNICORN_LOG_LEVEL:-info}" \
    --capture-output \
    --enable-stdio-inheritance

