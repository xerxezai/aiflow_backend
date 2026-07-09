"""
Merge Duplicate User
====================
Config-driven, idempotent management command that merges a *source* user
into a *target* user by reassigning every foreign key that references
``users(id)`` from source → target, then deleting the source user.

Designed to clean up biometric-sync placeholder users
(e.g. ``emp_JamalAyoub`` / ``JamalAyoub@radai.ae``) that duplicate a real
RBAC user (``jamal.ayoub`` / ``jamal.ayoub@rejlers.ae``).

Approach — SOFT-CODED:
  • Discovers FK constraints referencing ``users(id)`` from
    ``information_schema`` at runtime — no hardcoded table/column list.
  • For each referencing (table, column):
      - If UNIQUE / PK constraint exists on that column and the target
        already has a row for that FK value, the source row is deleted
        (target wins). Otherwise the FK is UPDATEd to point at target.
  • Wraps everything in a single transaction.

Usage:
    python manage.py merge_users \\
        --source-email JamalAyoub@radai.ae \\
        --target-email jamal.ayoub@rejlers.ae --dry-run

    python manage.py merge_users \\
        --source-email JamalAyoub@radai.ae \\
        --target-email jamal.ayoub@rejlers.ae
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

User = get_user_model()

USERS_TABLE = User._meta.db_table  # e.g. 'users'


class Command(BaseCommand):
    help = (
        'Merge a duplicate source user into a target user by reassigning all '
        'FK references and deleting the source. Idempotent, transactional.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--source-email', help='Duplicate user email to be merged away.')
        parser.add_argument('--source-username', help='Duplicate username (alternative to --source-email).')
        parser.add_argument('--target-email', help='Canonical user email to merge into.')
        parser.add_argument('--target-username', help='Canonical username (alternative to --target-email).')
        parser.add_argument('--dry-run', action='store_true', help='Report actions without writing.')

    # ------------------------------------------------------------------
    def _resolve_user(self, email, username, label):
        qs = User.objects.all()
        if email:
            qs = qs.filter(email__iexact=email)
        if username:
            qs = qs.filter(username__iexact=username)
        u = qs.first()
        if not u:
            raise CommandError(
                f'{label} user not found (email={email!r} username={username!r})'
            )
        return u

    def _discover_fks(self):
        """Return list of (table, column) referencing users(id)."""
        sql = """
            SELECT tc.table_name AS ref_table,
                   kcu.column_name AS ref_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
             AND tc.table_schema = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND ccu.table_name = %s
              AND ccu.column_name = 'id'
              AND tc.table_schema = 'public'
            ORDER BY ref_table, ref_column
        """
        with connection.cursor() as cur:
            cur.execute(sql, (USERS_TABLE,))
            return cur.fetchall()

    def _unique_columns(self, table):
        """Return set of columns that are covered by a single-column UNIQUE
        or PRIMARY KEY constraint on `table`. (Multi-column unique keys are
        not treated as conflicting on the FK column alone.)"""
        sql = """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.table_name = %s
              AND tc.table_schema = 'public'
              AND tc.constraint_type IN ('UNIQUE', 'PRIMARY KEY')
              AND kcu.column_name IN (
                  SELECT column_name FROM information_schema.key_column_usage
                  WHERE constraint_name = tc.constraint_name
              )
            GROUP BY tc.constraint_name, kcu.column_name
            HAVING COUNT(*) = 1
        """
        with connection.cursor() as cur:
            cur.execute(sql, (table,))
            return {r[0] for r in cur.fetchall()}

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        src = self._resolve_user(
            options.get('source_email'), options.get('source_username'), 'Source'
        )
        tgt = self._resolve_user(
            options.get('target_email'), options.get('target_username'), 'Target'
        )
        if src.id == tgt.id:
            raise CommandError('Source and target are the same user.')

        dry_run = options['dry_run']
        self.stdout.write(self.style.NOTICE(
            f'Source: id={src.id} email={src.email} username={src.username}'
        ))
        self.stdout.write(self.style.NOTICE(
            f'Target: id={tgt.id} email={tgt.email} username={tgt.username}'
        ))

        fks = self._discover_fks()
        self.stdout.write(f'Discovered {len(fks)} FK column(s) referencing {USERS_TABLE}(id).')

        # Plan
        plan = []  # (table, column, updates, deletes)
        for table, column in fks:
            with connection.cursor() as cur:
                cur.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" = %s', (src.id,)
                )
                n_src = cur.fetchone()[0]
                if not n_src:
                    continue

                uniques = self._unique_columns(table)
                # Conflict rows = rows in table where BOTH target already has same row-key
                # We use single-column UNIQUE on the FK column as the classic conflict shape.
                deletes = 0
                if column in uniques:
                    cur.execute(
                        f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" = %s', (tgt.id,)
                    )
                    if cur.fetchone()[0]:
                        deletes = n_src  # target already has one — drop source's row
                updates = 0 if deletes else n_src
                plan.append((table, column, updates, deletes))

        if not plan:
            self.stdout.write(self.style.SUCCESS('No FK rows point at source — nothing to merge.'))
        else:
            self.stdout.write('Merge plan:')
            for tbl, col, upd, dele in plan:
                if upd:
                    self.stdout.write(f'  UPDATE {tbl}.{col} : {upd} row(s) → {tgt.id}')
                if dele:
                    self.stdout.write(f'  DELETE from {tbl} where {col}={src.id} : {dele} row(s) (target already present)')
        self.stdout.write(f'  DELETE user id={src.id}')

        if dry_run:
            self.stdout.write(self.style.WARNING('--dry-run: no changes made.'))
            return

        with transaction.atomic():
            with connection.cursor() as cur:
                for tbl, col, upd, dele in plan:
                    if dele:
                        cur.execute(
                            f'DELETE FROM "{tbl}" WHERE "{col}" = %s', (src.id,)
                        )
                        self.stdout.write(f'  deleted {cur.rowcount} row(s) from {tbl}')
                    elif upd:
                        cur.execute(
                            f'UPDATE "{tbl}" SET "{col}" = %s WHERE "{col}" = %s',
                            (tgt.id, src.id),
                        )
                        self.stdout.write(f'  reassigned {cur.rowcount} row(s) in {tbl}.{col}')
            # Delete source user via raw SQL. Django's ORM collector walks every
            # FK declared in models — some may reference tables that do not exist
            # on this DB (feature not migrated yet). Since we've already reassigned
            # all real FK references above via information_schema introspection,
            # the raw DELETE is safe.
            with connection.cursor() as cur:
                cur.execute(f'DELETE FROM "{USERS_TABLE}" WHERE id = %s', (src.id,))

        self.stdout.write(self.style.SUCCESS(
            f'Merged user id={src.id} ({src.email}) into id={tgt.id} ({tgt.email}).'
        ))
