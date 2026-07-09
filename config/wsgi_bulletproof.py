"""
Bulletproof WSGI Application for Railway Production

This WSGI wrapper ALWAYS serves requests, even if Django fails to load.
It provides:
1. Working Django app when everything is OK
2. Fallback health check endpoint when Django fails
3. Always returns CORS headers for www.radai.ae

This prevents 502 Bad Gateway errors on Railway.
"""
import os
import sys
import json
import traceback
from datetime import datetime

# Try to load Django WSGI application
DJANGO_APP = None
DJANGO_ERROR = None

try:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    from django.core.wsgi import get_wsgi_application
    DJANGO_APP = get_wsgi_application()
    print("✅ Django WSGI application loaded successfully")
except Exception as e:
    DJANGO_ERROR = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
    print(f"⚠️  Django failed to load: {type(e).__name__}: {str(e)}")
    print("   Fallback WSGI app will handle requests")
    print(traceback.format_exc())


# SOFT-CODED CORS Configuration for fallback mode
FALLBACK_CORS_ORIGINS = [
    'https://www.radai.ae',
    'https://radai.ae',
    'http://www.radai.ae',
    'http://radai.ae',
    'http://localhost:5173',
    'http://localhost:3000',
]

FALLBACK_HEALTH_PATHS = [
    '/health/',
    '/api/v1/health/',
    '/api/v1/health/diagnostic/',
    '/',
]


def get_cors_headers(request_origin):
    """Get CORS headers based on request origin (soft-coded)"""
    headers = []
    
    if request_origin in FALLBACK_CORS_ORIGINS:
        headers.append(('Access-Control-Allow-Origin', request_origin))
    else:
        headers.append(('Access-Control-Allow-Origin', 'https://www.radai.ae'))
    
    headers.extend([
        ('Access-Control-Allow-Credentials', 'true'),
        ('Access-Control-Allow-Methods', 'GET, POST, PUT, PATCH, DELETE, OPTIONS'),
        ('Access-Control-Allow-Headers', 'Content-Type, Authorization, Accept, X-Requested-With'),
        ('Access-Control-Max-Age', '3600'),
    ])
    return headers


def fallback_app(environ, start_response):
    """
    Fallback WSGI application when Django fails to load.
    Always returns valid responses with CORS headers.
    """
    path = environ.get('PATH_INFO', '/')
    method = environ.get('REQUEST_METHOD', 'GET')
    origin = environ.get('HTTP_ORIGIN', '')
    
    # Get CORS headers based on request origin
    cors_headers = get_cors_headers(origin)
    
    # Handle OPTIONS preflight requests
    if method == 'OPTIONS':
        headers = [('Content-Type', 'text/plain'), ('Content-Length', '0')] + cors_headers
        start_response('200 OK', headers)
        return [b'']
    
    # Handle health check endpoints
    if path in FALLBACK_HEALTH_PATHS or path.startswith('/health') or path.startswith('/api/v1/health'):
        response_body = {
            'status': 'degraded',
            'service': 'radai-backend',
            'mode': 'fallback',
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'message': 'Backend is running in fallback mode - Django failed to load',
            'error_summary': DJANGO_ERROR.split('\n')[0] if DJANGO_ERROR else 'Unknown',
            'help': 'Check Railway logs for full Django import error',
        }
        body_bytes = json.dumps(response_body).encode('utf-8')
        headers = [
            ('Content-Type', 'application/json'),
            ('Content-Length', str(len(body_bytes))),
        ] + cors_headers
        start_response('200 OK', headers)
        return [body_bytes]
    
    # For all other requests, return service unavailable with helpful info
    response_body = {
        'error': 'service_unavailable',
        'message': 'Backend is starting up or Django failed to load',
        'mode': 'fallback',
        'path_requested': path,
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'help': 'Please try again in a few minutes. If persists, check Railway logs.',
    }
    body_bytes = json.dumps(response_body).encode('utf-8')
    headers = [
        ('Content-Type', 'application/json'),
        ('Content-Length', str(len(body_bytes))),
    ] + cors_headers
    start_response('503 Service Unavailable', headers)
    return [body_bytes]


def application(environ, start_response):
    """
    Main WSGI application entry point.
    Routes to Django if available, otherwise uses fallback.
    """
    if DJANGO_APP is not None:
        try:
            return DJANGO_APP(environ, start_response)
        except Exception as e:
            # Log the error but don't crash - use fallback
            print(f"⚠️  Django request failed: {type(e).__name__}: {str(e)}")
            print(traceback.format_exc())
            return fallback_app(environ, start_response)
    else:
        return fallback_app(environ, start_response)
