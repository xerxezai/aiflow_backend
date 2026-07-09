from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.users'
    
    def ready(self):
        """Import signal handlers when app is ready."""
        try:
            import apps.users.signals  # noqa: F401
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Could not load users.signals: {e}")
