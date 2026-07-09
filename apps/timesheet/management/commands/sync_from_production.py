"""
Management command: sync_from_production
=========================================
Pulls selected tables from the Railway production PostgreSQL database into
the local dev database using intelligent upsert (no data loss, no duplicates).

Usage:
    python manage.py sync_from_production                      # incremental, all tables
    python manage.py sync_from_production --mode full          # full replace, all tables
    python manage.py sync_from_production --list-tables        # show configured tables
    python manage.py sync_from_production --tables timesheet_timesheetevent payroll_employeeleaverecord
    python manage.py sync_from_production --since 2026-06-01   # override incremental cutoff
    python manage.py sync_from_production --dry-run            # preview without writing

Prerequisites:
    Add to backend/.env:
        PROD_DATABASE_URL=postgresql://postgres:<password>@<host>:<port>/railway

    Get the production public URL with:
        railway variables --service Postgres --json

Soft-coded:
    PROD_DATABASE_URL     — env var name for source connection string
    SYNC_BATCH_SIZE       — rows fetched/inserted per batch  (env: SYNC_BATCH_SIZE, default 500)
    SYNC_DEFAULT_MODE     — default sync mode                (env: SYNC_DEFAULT_MODE, default incremental)
    SYNC_TABLE_CONFIG     — per-table strategy dict (module-level constant, see below)
"""
from __future__ import annotations

import os
from typing import Any, Optional

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

# ── Soft-coded constants ──────────────────────────────────────────────────────

# Environment variable that holds the production public connection string
PROD_DATABASE_URL_ENVVAR: str = 'PROD_DATABASE_URL'

# How many rows to fetch/write in a single batch (keep memory bounded)
SYNC_BATCH_SIZE: int = int(os.environ.get('SYNC_BATCH_SIZE', '500'))

# Default sync mode when --mode is not supplied
SYNC_DEFAULT_MODE: str = os.environ.get('SYNC_DEFAULT_MODE', 'incremental')

# ── Table configuration ───────────────────────────────────────────────────────
# Each entry describes HOW to sync one table:
#   description   — human-readable label shown in progress output
#   timestamp_col — column used to detect "new" rows for incremental mode
#                   (None → always full-sync this table)
#   pk_col        — primary key column for ON CONFLICT upsert
#   order_col     — column used to ORDER the remote query (usually same as timestamp_col or pk)
#   priority      — lower = synced first (respect FK dependencies)

SYNC_TABLE_CONFIG: dict[str, dict[str, Any]] = {
    'rbac_userprofile': {
        'description':    'RAD AI user profiles (employee_id ↔ biometric code mapping)',
        'timestamp_col':  None,
        'pk_col':         'id',
        'order_col':      'id',
        'priority':       10,
    },
    'timesheet_biometricusermaster': {
        'description':    'Biometric device user master (name, email, department)',
        'timestamp_col':  None,
        'pk_col':         'id',
        'order_col':      'id',
        'priority':       20,
    },
    'timesheet_timesheetevent': {
        'description':    'Raw biometric swipe events',
        'timestamp_col':  'event_time',
        'pk_col':         'id',
        'order_col':      'event_time',
        'priority':       30,
    },
    'timesheet_dailyattendancesummary': {
        'description':    'Daily computed attendance summaries',
        'timestamp_col':  'date',
        'pk_col':         'id',
        'order_col':      'date',
        'priority':       40,
    },
    'payroll_employeeleaverecord': {
        'description':    'Employee annual-leave records (accrual balances)',
        'timestamp_col':  None,
        'pk_col':         'id',
        'order_col':      'id',
        'priority':       50,
    },
    'payroll_leaverequest': {
        'description':    'Employee leave-request submissions',
        'timestamp_col':  'created_at',
        'pk_col':         'id',
        'order_col':      'created_at',
        'priority':       60,
    },
}

# ─────────────────────────────────────────────────────────────────────────────


class Command(BaseCommand):
    help = 'Pull production PostgreSQL tables into local dev database (smart incremental upsert).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--mode',
            choices=['full', 'incremental'],
            default=SYNC_DEFAULT_MODE,
            help=(
                'full = upsert every row from production; '
                'incremental = only rows newer than local maximum (default: %(default)s)'
            ),
        )
        parser.add_argument(
            '--tables',
            nargs='+',
            metavar='TABLE',
            help='One or more table names to sync (default: all configured tables)',
        )
        parser.add_argument(
            '--since',
            metavar='YYYY-MM-DD',
            help='Incremental override: fetch rows whose timestamp_col > this date',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be synced without writing to local DB',
        )
        parser.add_argument(
            '--list-tables',
            action='store_true',
            help='Print all configured tables with their sync strategy and exit',
        )
        parser.add_argument(
            '--reset-sequences',
            action='store_true',
            default=True,
            help='After sync, reset PostgreSQL sequences to max(id) so new inserts work (default: True)',
        )
        parser.add_argument(
            '--no-reset-sequences',
            dest='reset_sequences',
            action='store_false',
            help='Skip sequence reset after sync',
        )

    # ── Entry point ───────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        if options['list_tables']:
            self._list_tables()
            return

        prod_url = os.environ.get(PROD_DATABASE_URL_ENVVAR)
        if not prod_url:
            raise CommandError(
                f'\n{PROD_DATABASE_URL_ENVVAR} is not set.\n'
                f'Add it to backend/.env:\n'
                f'  {PROD_DATABASE_URL_ENVVAR}=postgresql://postgres:<pass>@<host>:<port>/railway\n'
                f'\nGet the production public URL:\n'
                f'  railway variables --service Postgres --json\n'
            )

        tables_requested = options['tables'] or list(SYNC_TABLE_CONFIG.keys())
        unknown = [t for t in tables_requested if t not in SYNC_TABLE_CONFIG]
        if unknown:
            raise CommandError(
                f'Unknown table(s): {unknown}\n'
                f'Run --list-tables to see all configured tables.'
            )

        # Sort by priority so FK-parent tables sync before child tables
        tables = sorted(tables_requested, key=lambda t: SYNC_TABLE_CONFIG[t]['priority'])

        mode     = options['mode']
        since    = options['since']
        dry_run  = options['dry_run']
        reset_seq = options['reset_sequences']

        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n{prefix}sync_from_production — mode={mode}, tables={len(tables)}\n'
        ))

        try:
            import psycopg2  # noqa: PLC0415
        except ImportError:
            raise CommandError(
                'psycopg2 is not installed.\n'
                'It should already be in requirements.txt — '
                'run: pip install psycopg2-binary'
            )

        try:
            prod_conn = psycopg2.connect(prod_url)
            prod_conn.set_session(readonly=True, autocommit=True)
        except Exception as exc:
            raise CommandError(f'Cannot connect to production DB: {exc}')

        total_rows  = 0
        synced_tables = []

        try:
            for table in tables:
                cfg   = SYNC_TABLE_CONFIG[table]
                count = self._sync_table(prod_conn, table, cfg, mode, since, dry_run)
                total_rows += count
                synced_tables.append(table)
        finally:
            prod_conn.close()

        if reset_seq and not dry_run and synced_tables:
            self._reset_sequences(synced_tables)

        verb = 'Would sync' if dry_run else 'Synced'
        self.stdout.write(self.style.SUCCESS(
            f'\n{verb} {total_rows:,} rows across {len(synced_tables)} table(s).\n'
        ))

    # ── Per-table sync ────────────────────────────────────────────────────────

    def _sync_table(
        self,
        prod_conn,
        table: str,
        cfg: dict,
        mode: str,
        since_override: Optional[str],
        dry_run: bool,
    ) -> int:
        ts_col  = cfg['timestamp_col']
        pk_col  = cfg['pk_col']
        ord_col = cfg['order_col']

        # ── Determine incremental cutoff ──────────────────────────────────────
        since_val = None
        if mode == 'incremental' and ts_col:
            if since_override:
                since_val = since_override
            else:
                with connection.cursor() as cur:
                    cur.execute(f'SELECT MAX("{ts_col}") FROM "{table}"')  # noqa: S608
                    row = cur.fetchone()
                    since_val = row[0] if (row and row[0] is not None) else None

        # ── Build remote SELECT ───────────────────────────────────────────────
        prod_cur = prod_conn.cursor()
        if since_val is not None and ts_col:
            prod_cur.execute(
                f'SELECT * FROM "{table}" WHERE "{ts_col}" > %s ORDER BY "{ord_col}"',  # noqa: S608
                [since_val],
            )
            mode_label = f'incremental since {since_val}'
        else:
            prod_cur.execute(f'SELECT * FROM "{table}" ORDER BY "{ord_col}"')  # noqa: S608
            mode_label = 'full'

        # ── Discover column names from cursor metadata ────────────────────────
        cols        = [desc[0] for desc in prod_cur.description]
        col_list    = ', '.join(f'"{c}"' for c in cols)
        placeholders = ', '.join(['%s'] * len(cols))
        update_set  = ', '.join(
            f'"{c}" = EXCLUDED."{c}"' for c in cols if c != pk_col
        )

        upsert_sql = (
            f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders}) '
            f'ON CONFLICT ("{pk_col}") DO UPDATE SET {update_set}'
        )

        # ── Batch fetch → upsert ──────────────────────────────────────────────
        count = 0
        try:
            with transaction.atomic():
                batch = prod_cur.fetchmany(SYNC_BATCH_SIZE)
                while batch:
                    count += len(batch)
                    if not dry_run:
                        with connection.cursor() as local_cur:
                            local_cur.executemany(upsert_sql, batch)
                    batch = prod_cur.fetchmany(SYNC_BATCH_SIZE)
        except Exception as exc:
            prod_cur.close()
            self.stdout.write(self.style.ERROR(f'  ✗ {table}: FAILED — {exc}'))
            raise

        prod_cur.close()

        icon = self.style.SUCCESS('✓') if count > 0 else self.style.WARNING('–')
        verb = 'Would upsert' if dry_run else 'Upserted'
        self.stdout.write(f'  {icon} {table:<45} {verb} {count:>7,} rows  ({mode_label})')
        return count

    # ── Sequence reset ────────────────────────────────────────────────────────

    def _reset_sequences(self, tables: list[str]) -> None:
        """
        After inserting rows with explicit PKs, the PostgreSQL SERIAL sequence
        is not automatically advanced.  This resets each sequence to MAX(id)
        so subsequent inserts don't collide.
        """
        self.stdout.write('\n  Resetting sequences...')
        with connection.cursor() as cur:
            for table in tables:
                pk_col = SYNC_TABLE_CONFIG[table]['pk_col']
                try:
                    cur.execute(
                        "SELECT setval("
                        "  pg_get_serial_sequence(%s, %s), "
                        "  COALESCE((SELECT MAX(\"{pk}\") FROM \"{tbl}\"), 1)"
                        ")".format(pk=pk_col, tbl=table),
                        [table, pk_col],
                    )
                    self.stdout.write(f'    ✓ reset sequence for {table}.{pk_col}')
                except Exception as exc:
                    self.stdout.write(
                        self.style.WARNING(f'    ⚠ could not reset {table}.{pk_col}: {exc}')
                    )

    # ── List tables helper ────────────────────────────────────────────────────

    def _list_tables(self) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING('\nConfigured sync tables:\n'))
        sorted_cfg = sorted(SYNC_TABLE_CONFIG.items(), key=lambda x: x[1]['priority'])
        for table, cfg in sorted_cfg:
            ts_info = (
                f'incremental on "{cfg["timestamp_col"]}"'
                if cfg['timestamp_col']
                else 'full-replace only (no timestamp column)'
            )
            self.stdout.write(
                f'  [{cfg["priority"]:>3}]  {table:<45}  {cfg["description"]}\n'
                f'         {"pk=" + cfg["pk_col"]:<10}  {ts_info}'
            )
        self.stdout.write(
            f'\nTotal: {len(SYNC_TABLE_CONFIG)} table(s). '
            f'Add custom tables to SYNC_TABLE_CONFIG in this file.\n'
        )
