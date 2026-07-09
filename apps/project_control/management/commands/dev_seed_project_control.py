"""
DEV-ONLY: seed a realistic project-control portfolio for a target user.

Creates a mix of active projects (varying health), recent EVM cost snapshots,
upcoming/overdue milestones, assigned tasks, and detected change events —
so the Project Control persona dashboard shows meaningful, dynamic content
during local development.

Usage (inside container):
    python manage.py dev_seed_project_control jamal.ayoub@rejlers.ae
    python manage.py dev_seed_project_control jamal.ayoub@rejlers.ae --projects 6 --wipe

From host:
    docker exec aiflow_backend_local python manage.py dev_seed_project_control jamal.ayoub@rejlers.ae

REFUSES to run when DEBUG=False (safety — never touches production).
"""
import random
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

# ─── Soft-coded defaults ────────────────────────────────────────────────────
DEFAULT_PROJECT_COUNT = 5
SNAPSHOTS_PER_PROJECT = 8          # weekly snapshots → ~2 months of history
MILESTONES_PER_PROJECT = 4
TASKS_PER_PROJECT = 3
CHANGES_PER_PROJECT = 2
CODE_PREFIX = 'RAD-PC'

# Realistic Oil & Gas / EPC project templates. Each = one "flavour" of health.
PROJECT_TEMPLATES = [
    {
        'name':          'Ruwais Refinery — FEED Optimisation',
        'client':        'ADNOC Refining',
        'location':      'Ruwais, UAE',
        'scope':         'feed',
        'budget':        Decimal('4500000'),
        'contract':      Decimal('5250000'),
        'progress':      62,
        'priority':      'high',
        'cpi_range':     (0.98, 1.05),   # green
        'spi_range':     (0.96, 1.02),
        'spent_ratio':   0.58,
    },
    {
        'name':          'Habshan Compression — Detailed Engineering',
        'client':        'ADNOC Gas Processing',
        'location':      'Habshan, UAE',
        'scope':         'detailed_engineering',
        'budget':        Decimal('8200000'),
        'contract':      Decimal('9800000'),
        'progress':      45,
        'priority':      'critical',
        'cpi_range':     (0.86, 0.92),   # amber
        'spi_range':     (0.88, 0.94),
        'spent_ratio':   0.53,
    },
    {
        'name':          'Fujairah Terminal Expansion — EPCM',
        'client':        'Fujairah Oil Terminal FZE',
        'location':      'Fujairah, UAE',
        'scope':         'epcm',
        'budget':        Decimal('12500000'),
        'contract':      Decimal('14200000'),
        'progress':      28,
        'priority':      'critical',
        'cpi_range':     (0.74, 0.82),   # red
        'spi_range':     (0.78, 0.86),
        'spent_ratio':   0.42,
    },
    {
        'name':          'Duqm LNG Bunkering — Basic Engineering',
        'client':        'OQ (Oman)',
        'location':      'Duqm, Oman',
        'scope':         'basic_engineering',
        'budget':        Decimal('3200000'),
        'contract':      Decimal('3800000'),
        'progress':      74,
        'priority':      'medium',
        'cpi_range':     (1.01, 1.08),   # ahead
        'spi_range':     (0.98, 1.05),
        'spent_ratio':   0.70,
    },
    {
        'name':          'Jubail Petrochemical — Owner\'s Engineer',
        'client':        'SABIC',
        'location':      'Jubail, KSA',
        'scope':         'owner_engineer',
        'budget':        Decimal('2800000'),
        'contract':      Decimal('3100000'),
        'progress':      88,
        'priority':      'medium',
        'cpi_range':     (0.93, 0.99),
        'spi_range':     (0.90, 0.97),
        'spent_ratio':   0.84,
    },
    {
        'name':          'Sohar Refinery Turnaround — Consultancy',
        'client':        'ORPIC',
        'location':      'Sohar, Oman',
        'scope':         'pmc',
        'budget':        Decimal('1900000'),
        'contract':      Decimal('2100000'),
        'progress':      15,
        'priority':      'high',
        'cpi_range':     (0.88, 0.95),
        'spi_range':     (0.82, 0.90),
        'spent_ratio':   0.19,
    },
]

MILESTONE_NAMES = [
    'FEED Package Handover',
    'HAZOP Study Close-out',
    '30% Model Review',
    '60% Model Review',
    '90% Model Review',
    'IFC Drawings Issued',
    'Long-Lead Equipment PO',
    'Client Sign-off — Basis of Design',
    'Cost Estimate Class-3',
    'Construction Kick-off',
]

TASK_TITLES = [
    'Review updated P&IDs — Unit 200',
    'Consolidate change register for weekly cost meeting',
    'Prepare EVM report for client presentation',
    'Verify cost-loaded schedule vs baseline',
    'Reconcile PO commitments with cost snapshot',
    'Draft variation order — pipeline scope creep',
    'Update risk register — critical path items',
    'QA/QC review — instrument index Rev-B',
]

CHANGE_SUMMARIES = [
    ('Scope addition — additional 3\" line for HP flare header', 'medium',  75000),
    ('Client-requested MOC valve upgrade — API 6D → 6A',         'high',    145000),
    ('Piping ISO revision — reroute due to structural clash',    'medium',   32000),
    ('Instrument philosophy change — SIL-3 → SIL-2',             'low',      -18000),
    ('Additional PSV sizing calcs per updated ASME',             'medium',   22500),
    ('Compression train re-rating — 12 MW → 15 MW',              'critical', 380000),
]

User = get_user_model()


class Command(BaseCommand):
    help = 'DEV-ONLY: seed a realistic project-control portfolio for a user. Refuses in production.'

    def add_arguments(self, parser):
        parser.add_argument('email', help='Owner user email')
        parser.add_argument('--projects', type=int, default=DEFAULT_PROJECT_COUNT,
                            help=f'How many projects to create (default: {DEFAULT_PROJECT_COUNT})')
        parser.add_argument('--wipe', action='store_true',
                            help='Delete any existing seeded projects (code starts with RAD-PC) for this user first')

    def handle(self, *args, **opts):
        env = str(getattr(settings, 'ENVIRONMENT', '')).lower()
        if env == 'production' or not getattr(settings, 'DEBUG', False):
            raise CommandError(
                f'REFUSED: this command is DEV-only (ENVIRONMENT={env or "?"}, DEBUG={settings.DEBUG}).'
            )

        email = opts['email']
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise CommandError(f'User not found: {email}')

        from apps.core.project_models import Project, ProjectMember, ProjectTask, ProjectMilestone
        from apps.project_control.models import CostSnapshot, ChangeEvent

        n = max(1, min(opts['projects'], len(PROJECT_TEMPLATES)))
        templates = PROJECT_TEMPLATES[:n]

        with transaction.atomic():
            if opts['wipe']:
                deleted = Project.objects.filter(owner=user, code__startswith=CODE_PREFIX).delete()
                self.stdout.write(self.style.WARNING(f'Wiped {deleted[0]} rows for seeded projects'))

            created_projects = []
            today = timezone.now().date()

            for idx, tpl in enumerate(templates, start=1):
                code = f'{CODE_PREFIX}-{idx:03d}'
                start = today - timedelta(days=random.randint(90, 240))
                end = today + timedelta(days=random.randint(60, 210))
                spent = (tpl['budget'] * Decimal(str(tpl['spent_ratio']))).quantize(Decimal('0.01'))

                project, was_created = Project.objects.update_or_create(
                    code=code,
                    defaults=dict(
                        name=tpl['name'],
                        description=f'DEV SEED · {tpl["scope"]} scope · {tpl["client"]}',
                        owner=user,
                        status='active',
                        priority=tpl['priority'],
                        progress=tpl['progress'],
                        start_date=start,
                        end_date=end,
                        budget=tpl['budget'],
                        spent=spent,
                        contract_value=tpl['contract'],
                        currency='AED',
                        scope_type=tpl['scope'],
                        client_name=tpl['client'],
                        location=tpl['location'],
                        is_deleted=False,
                    ),
                )
                ProjectMember.objects.get_or_create(
                    project=project, user=user,
                    defaults={'role': 'project_manager', 'is_active': True},
                )
                created_projects.append((project, tpl, was_created))

            # Weekly EVM snapshots — walk backwards, so latest ends at "today"
            for project, tpl, _ in created_projects:
                CostSnapshot.objects.filter(project=project).delete()
                for w in range(SNAPSHOTS_PER_PROJECT):
                    period_end = today - timedelta(days=7 * w)
                    cpi = round(random.uniform(*tpl['cpi_range']), 2)
                    spi = round(random.uniform(*tpl['spi_range']), 2)
                    # earned/planned/actual scaled from budget × progress fraction
                    pv = tpl['budget'] * Decimal(str(round(0.10 + w * 0.05, 2)))
                    ev = (pv * Decimal(str(spi))).quantize(Decimal('0.01'))
                    ac = (ev / Decimal(str(cpi))).quantize(Decimal('0.01'))
                    eac = (tpl['budget'] / Decimal(str(cpi))).quantize(Decimal('0.01'))
                    CostSnapshot.objects.create(
                        project=project,
                        period_end=period_end,
                        planned_value=pv.quantize(Decimal('0.01')),
                        earned_value=ev,
                        actual_cost=ac,
                        cpi=cpi, spi=spi, eac=eac,
                        source='manual',
                        notes='DEV SEED',
                    )

            # Milestones — spread over ±60 days for a lively timeline
            for project, tpl, _ in created_projects:
                ProjectMilestone.objects.filter(project=project).delete()
                names = random.sample(MILESTONE_NAMES, MILESTONES_PER_PROJECT)
                # offsets: -14 (overdue), +5, +18, +40 → mix of overdue, urgent, soon, later
                offsets = [-14, 5, 18, 40]
                for name, offset in zip(names, offsets):
                    ProjectMilestone.objects.create(
                        project=project,
                        name=name,
                        description='DEV SEED',
                        target_date=today + timedelta(days=offset),
                        is_completed=False,
                    )

            # Tasks — some overdue, some due this week, some later
            for project, tpl, _ in created_projects:
                ProjectTask.objects.filter(project=project, assigned_to=user).delete()
                titles = random.sample(TASK_TITLES, TASKS_PER_PROJECT)
                offsets = [-3, 4, 12]
                statuses = ['in_progress', 'todo', 'review']
                priorities = ['high', 'medium', 'critical']
                for title, offset, st, pr in zip(titles, offsets, statuses, priorities):
                    ProjectTask.objects.create(
                        project=project,
                        title=title,
                        description='DEV SEED',
                        status=st,
                        assigned_to=user,
                        due_date=today + timedelta(days=offset),
                        priority=pr,
                        estimated_hours=Decimal(str(random.choice([4, 8, 16, 24]))),
                    )

            # Change events — recent, mixed severity
            for project, tpl, _ in created_projects:
                ChangeEvent.objects.filter(project=project).delete()
                picks = random.sample(CHANGE_SUMMARIES, CHANGES_PER_PROJECT)
                for offset, (summary, severity, delta) in enumerate(picks, start=1):
                    ce = ChangeEvent.objects.create(
                        project=project,
                        summary=summary,
                        description='DEV SEED',
                        severity=severity,
                        delta_amount=Decimal(str(delta)),
                        delta_currency='AED',
                        status='detected',
                        ai_confidence=round(random.uniform(0.72, 0.94), 2),
                    )
                    # Back-date detected_at so timeline is spread
                    ChangeEvent.objects.filter(pk=ce.pk).update(
                        detected_at=timezone.now() - timedelta(hours=offset * 6 + random.randint(0, 4)),
                    )

        self.stdout.write(self.style.SUCCESS(
            f'\nSeeded {len(created_projects)} projects for {user.email}:'
        ))
        for project, tpl, was_created in created_projects:
            flag = 'created' if was_created else 'updated'
            self.stdout.write(
                f'  · {project.code} — {project.name[:55]:55s} [{flag}]  '
                f'budget=AED {tpl["budget"]:>10,.0f}  progress={tpl["progress"]}%'
            )
        self.stdout.write(self.style.SUCCESS(
            f'\nSnapshots: {SNAPSHOTS_PER_PROJECT * len(created_projects)}  '
            f'Milestones: {MILESTONES_PER_PROJECT * len(created_projects)}  '
            f'Tasks: {TASKS_PER_PROJECT * len(created_projects)}  '
            f'Changes: {CHANGES_PER_PROJECT * len(created_projects)}'
        ))
