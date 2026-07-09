"""
Enquiry URL Configuration
Public endpoints for customer enquiries + admin management endpoints.
"""
from django.urls import path
from .views_enquiry import (
    submit_enquiry,
    list_enquiries,
    enquiry_detail,
    enquiry_stats,
)

urlpatterns = [
    # Public
    path('submit/', submit_enquiry, name='submit-enquiry'),

    # Admin (IsAdminUser)
    path('',             list_enquiries, name='list-enquiries'),
    path('stats/',       enquiry_stats,  name='enquiry-stats'),
    path('<int:pk>/',    enquiry_detail, name='enquiry-detail'),
]
