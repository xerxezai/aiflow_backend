"""
Password Expiry Middleware
Automatically checks and enforces password expiry policy on each request
"""
from django.utils import timezone
from django.http import JsonResponse
from django.db.utils import OperationalError
from config.password_policy import get_password_expiry_status
import logging

logger = logging.getLogger(__name__)


class PasswordExpiryMiddleware:
    """
    Middleware to check password expiry on each authenticated request
    Soft-coded approach with configurable policies
    """
    
    # Endpoints that should bypass password expiry check
    EXEMPT_PATHS = [
        '/api/users/check-first-login/',
        '/api/users/reset-first-login-password/',
        '/api/users/change-password/',
        '/api/users/request-password-reset/',
        '/api/users/verify-reset-token/',
        '/api/users/reset-password/',
        '/api/users/check-password-expiry/',
        '/api/auth/login/',
        '/api/auth/logout/',
        '/api/auth/token/refresh/',
        '/admin/',
        '/static/',
        '/media/',
    ]
    
    def __init__(self, get_response):
        """Initialize middleware"""
        self.get_response = get_response
    
    def __call__(self, request):
        """Process request"""
        # Check password expiry before processing request
        try:
            response = self._check_password_expiry(request)
        except OperationalError:
            # Authentication is session-backed, so a temporary database/DNS
            # outage can occur while Django evaluates request.user. Surface a
            # retryable service response instead of an internal-server error.
            logger.warning('Database temporarily unavailable while evaluating the authenticated session')
            response = JsonResponse({
                'error': 'database_temporarily_unavailable',
                'detail': 'The database is temporarily unavailable. Please retry shortly.',
            }, status=503)
            response['Retry-After'] = '5'
            return response
        
        if response:
            return response
        
        # Continue with normal request processing
        response = self.get_response(request)
        return response
    
    def _check_password_expiry(self, request):
        """
        Check if user's password has expired
        
        Returns:
            JsonResponse if password expired and action required, None otherwise
        """
        # Skip if user not authenticated
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return None
        
        # Skip exempt paths
        path = request.path
        if any(path.startswith(exempt) for exempt in self.EXEMPT_PATHS):
            return None
        
        user = request.user
        
        # Get password expiry status
        expiry_status = get_password_expiry_status(user)
        
        # If user is exempt, allow access
        if expiry_status.get('exempt'):
            return None
        
        # If password requires immediate change (expired beyond grace period)
        if expiry_status.get('requires_change'):
            logger.warning(
                f"User {user.email} attempted access with expired password "
                f"(expired {abs(expiry_status['days_until_expiry'])} days ago)"
            )
            
            return JsonResponse({
                'error': 'password_expired',
                'message': 'Your password has expired. Please reset your password to continue.',
                'days_overdue': abs(expiry_status['days_until_expiry']),
                'must_reset_password': True,
                'redirect_to': '/change-password'
            }, status=403)
        
        # If in grace period, add warning header but allow access
        if expiry_status.get('in_grace_period'):
            logger.info(
                f"User {user.email} is in password expiry grace period "
                f"({abs(expiry_status['days_until_expiry'])} days overdue)"
            )
            # Continue with request but could add header warning
            # Will be handled in the response
        
        return None
    
    def process_response(self, request, response):
        """
        Add password expiry warnings to response headers
        """
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return response
        
        # Get password expiry status
        expiry_status = get_password_expiry_status(request.user)
        
        # Add warning headers if in warning or grace period
        if expiry_status.get('in_warning_period'):
            response['X-Password-Expiry-Warning'] = 'true'
            response['X-Password-Days-Until-Expiry'] = str(expiry_status['days_until_expiry'])
        
        if expiry_status.get('in_grace_period'):
            response['X-Password-Expiry-Grace'] = 'true'
            response['X-Password-Days-Overdue'] = str(abs(expiry_status['days_until_expiry']))
        
        return response
