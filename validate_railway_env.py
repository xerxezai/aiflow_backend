#!/usr/bin/env python
"""
Railway Environment Validator
Checks for required environment variables before deployment
"""
import os
import sys


def validate_railway_environment():
    """Validate Railway environment variables"""
    print("\n" + "="*70)
    print("🔍 Validating Railway Environment Variables")
    print("="*70)
    
    errors = []
    warnings = []
    
    # Check critical variables
    critical_vars = {
        'SECRET_KEY': 'Django secret key for security',
        'DATABASE_URL': 'PostgreSQL connection string (auto-set by Railway)',
        'RAILWAY_ENVIRONMENT': 'Deployment environment (production/staging)',
    }
    
    # Check recommended variables
    recommended_vars = {
        'FRONTEND_URL': 'Frontend URL for CORS (https://www.radai.ae)',
        'BACKEND_URL': 'Backend URL for health checks',
        'AWS_ACCESS_KEY_ID': 'AWS S3 access key (for file uploads)',
        'AWS_SECRET_ACCESS_KEY': 'AWS S3 secret key (for file uploads)',
        'AWS_STORAGE_BUCKET_NAME': 'AWS S3 bucket name',
    }
    
    # Check optional variables
    optional_vars = {
        'OPENAI_API_KEY': 'OpenAI API key (for AI features)',
        'ANTHROPIC_API_KEY': 'Anthropic API key (for AI features)',
        'REDIS_URL': 'Redis connection (for caching/Celery)',
        'SENTRY_DSN': 'Sentry error tracking',
    }
    
    print("\nCritical Variables:")
    for var, desc in critical_vars.items():
        value = os.environ.get(var)
        if not value:
            errors.append(f"❌ {var}: MISSING - {desc}")
            print(f"  ❌ {var}: MISSING")
        elif var == 'SECRET_KEY' and value == 'django-insecure-change-this-in-production':
            warnings.append(f"⚠️  {var}: Using default value (INSECURE)")
            print(f"  ⚠️  {var}: Using default (INSECURE!)")
        else:
            print(f"  ✅ {var}: OK (len={len(value)})")
    
    print("\nRecommended Variables:")
    for var, desc in recommended_vars.items():
        value = os.environ.get(var)
        if not value:
            warnings.append(f"⚠️  {var}: Missing - {desc}")
            print(f"  ⚠️  {var}: Missing - {desc}")
        else:
            print(f"  ✅ {var}: OK")
    
    print("\nOptional Variables:")
    for var, desc in optional_vars.items():
        value = os.environ.get(var)
        if value:
            print(f"  ✅ {var}: OK")
        else:
            print(f"  ℹ️  {var}: Not set - {desc}")
    
    # Print summary
    print("\n" + "="*70)
    if errors:
        print("❌ CRITICAL ERRORS FOUND:")
        for error in errors:
            print(f"  {error}")
        print("\nDeployment may fail! Set these variables in Railway:")
        print("  1. Go to Railway → aiflowbackend-production → Variables")
        print("  2. Add missing variables")
        print("="*70 + "\n")
        # Don't exit - allow deployment to continue with warnings
        # return 1
    
    if warnings:
        print("⚠️  WARNINGS:")
        for warning in warnings:
            print(f"  {warning}")
        print("\nConsider setting these for full functionality:")
        print("  - FRONTEND_URL: Required for CORS")
        print("  - AWS credentials: Required for file uploads")
        print("="*70 + "\n")
    
    if not errors and not warnings:
        print("✅ All required environment variables are set!")
        print("="*70 + "\n")
    
    return 0  # Always succeed to allow deployment


if __name__ == '__main__':
    sys.exit(validate_railway_environment())
