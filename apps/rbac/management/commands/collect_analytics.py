"""
Manually trigger the admin dashboard analytics collectors.

Usage:
    python manage.py collect_analytics            # honour TTL gates
    python manage.py collect_analytics --force    # rebuild snapshots now

Designed for cron / Celery beat / first-run bootstrap so the admin
dashboard has data even before the first admin loads the page.
"""
from django.core.management.base import BaseCommand

from apps.rbac.analytics_collectors import ensure_fresh


class Command(BaseCommand):
    help = 'Populate admin dashboard analytics tables from live sources.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Bypass TTL gates and rebuild every snapshot.',
        )

    def handle(self, *args, **options):
        result = ensure_fresh(force=bool(options.get('force')))
        for key, value in result.items():
            self.stdout.write(self.style.SUCCESS(f'  {key}: {value}'))
        self.stdout.write(self.style.SUCCESS('Analytics collectors finished.'))
