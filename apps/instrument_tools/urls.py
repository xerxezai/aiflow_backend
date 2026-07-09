from django.urls import path

from .views import (
    IOListView,
    CableBlockDiagramView,
    CableScheduleView,
    MetaView,
)

app_name = 'instrument_tools'

# Soft-coded URL stems — keep these in sync with the frontend service file.
urlpatterns = [
    path('meta/',                  MetaView.as_view(),              name='meta'),
    path('io-list/',               IOListView.as_view(),            name='io-list'),
    path('cable-block-diagram/',   CableBlockDiagramView.as_view(), name='cable-block-diagram'),
    path('cable-schedule/',        CableScheduleView.as_view(),     name='cable-schedule'),
]
