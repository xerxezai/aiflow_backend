"""
URLs for user management and authentication
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views_password import (
    check_first_login,
    reset_first_login_password,
    validate_email,
    change_password,
    check_password_expiry,
    request_password_reset,
    verify_reset_token,
    reset_password_with_token,
)
from .views import EmployeeProfileViewSet

# Router for employee profile endpoints
router = DefaultRouter()
router.register(r'employees', EmployeeProfileViewSet, basename='employees')

urlpatterns = [
    path('check-first-login/', check_first_login, name='check-first-login'),
    path('reset-first-login-password/', reset_first_login_password, name='reset-first-login-password'),
    path('validate-email/', validate_email, name='validate-email'),
    path('change-password/', change_password, name='change-password'),
    path('check-password-expiry/', check_password_expiry, name='check-password-expiry'),
    
    # Password reset endpoints
    path('request-password-reset/', request_password_reset, name='request-password-reset'),
    path('verify-reset-token/', verify_reset_token, name='verify-reset-token'),
    path('reset-password/', reset_password_with_token, name='reset-password-with-token'),
    
    # Employee profile management
    path('', include(router.urls)),
]

