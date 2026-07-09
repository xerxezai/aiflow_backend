from django.urls import path

from .views import realtime_snapshot

app_name = 'marketing_analytics'

urlpatterns = [
    path('realtime/', realtime_snapshot, name='realtime-snapshot'),
]
