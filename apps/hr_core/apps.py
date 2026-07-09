"""
HR Core Django app configuration.
"""
from django.apps import AppConfig


class HrCoreConfig(AppConfig):
    """Configuration for HR Core app."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.hr_core'
    verbose_name = 'HR Core - Employee Master'
    
    def ready(self):
        """Import signals when app is ready."""
        try:
            import apps.hr_core.signals  # noqa
        except ImportError:
            pass
