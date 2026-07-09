"""
Management command: seed_public_holidays
========================================
Populates the PublicHoliday table with the official Abu Dhabi / UAE government
public holiday calendar for the specified year(s).

Holiday data is soft-coded in HOLIDAY_SEED below — update the list each year
without touching any migration or model.  Dates that are "subject to moon
sighting" (e.g. Eid) are marked in the note field.

Usage:
    python manage.py seed_public_holidays             # seed current + next year
    python manage.py seed_public_holidays --year 2027
    python manage.py seed_public_holidays --year 2026 --year 2027
    python manage.py seed_public_holidays --force     # overwrite existing entries
"""
from __future__ import annotations

import datetime
from django.core.management.base import BaseCommand
from apps.payroll.models import PublicHoliday


# ─────────────────────────────────────────────────────────────────────────────
# Abu Dhabi / UAE Official Public Holiday Calendar
#
# Source: Abu Dhabi Government Portal (https://www.adgm.com) and UAE Federal
#         Authority for Government Human Resources (FAHR).
# Format: (MM-DD, name_en, name_ar, region, note)
#
# IMPORTANT: Hijri-based holidays shift by ~11 days each Gregorian year and
# are subject to moon sighting — approximate dates used here.  HR should
# update or confirm exact dates from the official FAHR announcement each year.
# ─────────────────────────────────────────────────────────────────────────────

_HOLIDAY_SEED: dict[int, list[tuple]] = {
    # Format: (MM-DD, English Name, Arabic Name, region, note)
    2026: [
        ('01-01', "New Year's Day",                 'رأس السنة الميلادية',     'AE',    ''),
        ('03-20', 'Isra and Mi\'raj (Prophet\'s Ascension)', 'ليلة المعراج',   'AE',    'Subject to moon sighting'),
        ('03-30', 'First Day of Ramadan',           'أول رمضان',               'AE',    'Subject to moon sighting — approximate date'),
        ('04-28', 'Eid Al Fitr Eve',                'وقفة عيد الفطر',          'AE',    'Subject to moon sighting'),
        ('04-29', 'Eid Al Fitr Day 1',              'عيد الفطر — اليوم الأول', 'AE',    'Subject to moon sighting'),
        ('04-30', 'Eid Al Fitr Day 2',              'عيد الفطر — اليوم الثاني','AE',    'Subject to moon sighting'),
        ('05-01', 'Eid Al Fitr Day 3',              'عيد الفطر — اليوم الثالث','AE',    'Subject to moon sighting'),
        ('07-05', 'Arafat (Eid Al Adha Eve)',       'يوم عرفة',                'AE',    'Subject to moon sighting'),
        ('07-06', 'Eid Al Adha Day 1',              'عيد الأضحى — اليوم الأول','AE',    'Subject to moon sighting'),
        ('07-07', 'Eid Al Adha Day 2',              'عيد الأضحى — اليوم الثاني','AE',   'Subject to moon sighting'),
        ('07-08', 'Eid Al Adha Day 3',              'عيد الأضحى — اليوم الثالث','AE',   'Subject to moon sighting'),
        ('07-26', 'Islamic New Year (Hijri New Year)', 'رأس السنة الهجرية',    'AE',    'Subject to moon sighting — approximate date'),
        ('10-04', 'Prophet\'s Birthday (Mawlid)',   'المولد النبوي الشريف',    'AE',    'Subject to moon sighting — approximate date'),
        ('11-15', 'Commemoration Day',              'يوم الشهيد',              'AE',    'UAE — Martyrs\' Day'),
        ('12-02', 'UAE National Day',               'اليوم الوطني الإماراتي',  'AE',    ''),
        ('12-03', 'UAE National Day (Holiday)',     'عطلة اليوم الوطني',       'AE',    ''),
    ],
    2027: [
        ('01-01', "New Year's Day",                 'رأس السنة الميلادية',     'AE',    ''),
        ('03-10', 'Isra and Mi\'raj (Prophet\'s Ascension)', 'ليلة المعراج',   'AE',    'Subject to moon sighting — approximate'),
        ('03-19', 'First Day of Ramadan',           'أول رمضان',               'AE',    'Subject to moon sighting — approximate'),
        ('04-17', 'Eid Al Fitr Eve',                'وقفة عيد الفطر',          'AE',    'Subject to moon sighting'),
        ('04-18', 'Eid Al Fitr Day 1',              'عيد الفطر — اليوم الأول', 'AE',    'Subject to moon sighting'),
        ('04-19', 'Eid Al Fitr Day 2',              'عيد الفطر — اليوم الثاني','AE',    'Subject to moon sighting'),
        ('04-20', 'Eid Al Fitr Day 3',              'عيد الفطر — اليوم الثالث','AE',    'Subject to moon sighting'),
        ('06-25', 'Arafat (Eid Al Adha Eve)',       'يوم عرفة',                'AE',    'Subject to moon sighting'),
        ('06-26', 'Eid Al Adha Day 1',              'عيد الأضحى — اليوم الأول','AE',    'Subject to moon sighting'),
        ('06-27', 'Eid Al Adha Day 2',              'عيد الأضحى — اليوم الثاني','AE',   'Subject to moon sighting'),
        ('06-28', 'Eid Al Adha Day 3',              'عيد الأضحى — اليوم الثالث','AE',   'Subject to moon sighting'),
        ('07-15', 'Islamic New Year (Hijri New Year)', 'رأس السنة الهجرية',    'AE',    'Subject to moon sighting — approximate'),
        ('09-23', 'Prophet\'s Birthday (Mawlid)',   'المولد النبوي الشريف',    'AE',    'Subject to moon sighting — approximate'),
        ('11-15', 'Commemoration Day',              'يوم الشهيد',              'AE',    'UAE — Martyrs\' Day'),
        ('12-02', 'UAE National Day',               'اليوم الوطني الإماراتي',  'AE',    ''),
        ('12-03', 'UAE National Day (Holiday)',     'عطلة اليوم الوطني',       'AE',    ''),
    ],
}


class Command(BaseCommand):
    help = (
        'Seed the PublicHoliday table with official Abu Dhabi / UAE government '
        'holidays for the given year(s).'
    )

    def add_arguments(self, parser):
        today = datetime.date.today()
        parser.add_argument(
            '--year',
            action='append',
            type=int,
            default=None,
            dest='years',
            help='Year(s) to seed (default: current year and next year).',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            default=False,
            help='Overwrite existing government-seeded entries (HR-added entries are never overwritten).',
        )

    def handle(self, *args, **options):
        from apps.payroll.models import PublicHoliday

        today  = datetime.date.today()
        years  = options['years'] or [today.year, today.year + 1]
        force  = options['force']
        created_count = 0
        skipped_count = 0
        updated_count = 0

        for year in years:
            entries = _HOLIDAY_SEED.get(year)
            if not entries:
                self.stdout.write(
                    self.style.WARNING(f'  No seed data for {year} — skipping.')
                )
                continue

            self.stdout.write(f'  Seeding {len(entries)} holidays for {year}…')

            for mm_dd, name_en, name_ar, region, note in entries:
                date = datetime.date(year, int(mm_dd.split('-')[0]), int(mm_dd.split('-')[1]))
                obj  = PublicHoliday.objects.filter(date=date, region=region).first()

                if obj is None:
                    PublicHoliday.objects.create(
                        date=date,
                        name=name_en,
                        name_ar=name_ar,
                        region=region,
                        note=note,
                        source='government',
                        is_active=True,
                    )
                    created_count += 1
                elif obj.source == 'hr_added':
                    # Never overwrite HR-added entries with seed data
                    skipped_count += 1
                elif force:
                    obj.name    = name_en
                    obj.name_ar = name_ar
                    obj.note    = note
                    obj.is_active = True
                    obj.save(update_fields=['name', 'name_ar', 'note', 'is_active', 'updated_at'])
                    updated_count += 1
                else:
                    skipped_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Done — created: {created_count}, updated: {updated_count}, skipped: {skipped_count}'
            )
        )
        self.stdout.write(
            '  Run with --force to overwrite existing government entries.\n'
            '  Run again after official FAHR announcements to correct moon-sighting dates.'
        )
