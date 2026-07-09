from django.apps import AppConfig


class InstrumentToolsConfig(AppConfig):
    """Lightweight Instrument Engineering helpers.

    Provides Generator / QC services for:
      • IO List
      • Cable Block Diagram
      • Cable Schedule

    Stateless (no DB models) — keeps the surface area small and avoids
    interacting with the core Instrument Index / Datasheet models.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.instrument_tools'
    verbose_name = 'Instrument Tools (IO List / Cable Block / Cable Schedule)'
