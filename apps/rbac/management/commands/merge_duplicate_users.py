"""
Merge All Biometric-Sync Duplicate Users
========================================
Batch cleanup of duplicate user accounts created by the biometric mirror
sync agent. For each detected pair, all FKs referencing ``users(id)`` are
reassigned from the placeholder to the canonical user, then the
placeholder user row is deleted.

Detection is soft-coded via module-level constants below:
  PLACEHOLDER_USERNAME_PREFIX
  PLACEHOLDER_EMAIL_DOMAIN
  BIOMETRIC_CODE_STORED_IN     (which User field holds the biometric code)
  BIOMETRIC_CODE_STRIP_SUFFIX  (e.g., ``.0`` from float-cast codes)

Linking rule:
  placeholder.first_name (numeric) → biometric employee code →
  match against rbac_user_profiles.employee_id → canonical user.

Only pairs where the canonical user is NOT itself a placeholder are merged.
Unmatched placeholders are reported but left untouched (they are
biometric-only accounts, not duplicates).

Usage:
    python manage.py merge_duplicate_users --dry-run
    python manage.py merge_duplicate_users
    python manage.py merge_duplicate_users --limit 10
"""
import re

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection, transaction

User = get_user_model()
USERS_TABLE = User._meta.db_table

# ─── Soft-coded detection knobs ─────────────────────────────────────────
PLACEHOLDER_USERNAME_PREFIX = 'emp_'
PLACEHOLDER_EMAIL_DOMAIN    = '@radai.ae'
BIOMETRIC_CODE_STORED_IN    = 'first_name'   # column on `users` holding the code
BIOMETRIC_CODE_REGEX        = re.compile(r'^(\d+)(?:\.0+)?$')
PROFILE_TABLE               = 'rbac_user_profiles'
PROFILE_EMP_ID_COL          = 'employee_id'


class Command(BaseCommand):
    help = (
        'Detect placeholder users created by biometric sync and merge each '
        'one into its canonical RBAC user (matched via employee_id). '
        'Idempotent. Reassigns all FK references soft-coded via information_schema.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report pairs and actions without writing to DB.')
        parser.add_argument('--limit', type=int, default=None,
                            help='Only merge the first N pairs (useful for staged rollouts).')

    # ------------------------------------------------------------------
    def _biometric_code(self, raw):
        raw = (raw or '').strip()
        if not raw:
            return None
        m = BIOMETRIC_CODE_REGEX.match(raw)
        return m.group(1) if m else raw

    def _discover_pairs(self, cur):
        cur.execute(
            f'''SELECT id, username, email, {BIOMETRIC_CODE_STORED_IN} AS code_raw, is_active
                FROM "{USERS_TABLE}"
                WHERE username ILIKE %s OR email ILIKE %s
                ORDER BY id''',
            (f'{PLACEHOLDER_USERNAME_PREFIX}%', f'%{PLACEHOLDER_EMAIL_DOMAIN}'),
        )
        placeholders = cur.fetchall()
        pairs = []
        unmatched = []
        for pid, puname, pemail, code_raw, pactive in placeholders:
            code = self._biometric_code(code_raw)
            if not code:
                unmatched.append((pid, puname, pemail, 'no biometric code'))
                continue
            cur.execute(
                f'''SELECT u.id, u.username, u.email, u.is_active
                    FROM "{USERS_TABLE}" u
                    JOIN "{PROFILE_TABLE}" p ON p.user_id = u.id
                    WHERE p."{PROFILE_EMP_ID_COL}" = %s
                      AND u.id <> %s
                    ORDER BY u.is_active DESC, u.id''',
                (code, pid),
            )
            matches = cur.fetchall()
            canonical = None
            for cid, cuname, cemail, cactive in matches:
                is_ph = (
                    (cuname or '').lower().startswith(PLACEHOLDER_USERNAME_PREFIX)
                    or (cemail or '').lower().endswith(PLACEHOLDER_EMAIL_DOMAIN)
                )
                if not is_ph:
                    canonical = (cid, cuname, cemail, cactive)
                    break
            if canonical is None:
                unmatched.append((pid, puname, pemail, f'no canonical for code={code}'))
                continue
            pairs.append(((pid, puname, pemail), canonical, code))
        return pairs, unmatched

    def _discover_fks(self, cur):
        cur.execute("""
            SELECT tc.table_name, kcu.column_name
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
        """, (USERS_TABLE,))
        return cur.fetchall()

    def _single_col_uniques(self, cur, table):
        cur.execute("""
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.table_name = %s
              AND tc.table_schema = 'public'
              AND tc.constraint_type IN ('UNIQUE', 'PRIMARY KEY')
            GROUP BY tc.constraint_name, kcu.column_name
            HAVING COUNT(*) = 1
        """, (table,))
        return {r[0] for r in cur.fetchall()}

    def _merge_one(self, cur, src_id, tgt_id, fks, uniques_cache):
        """Reassign all FK rows from src_id to tgt_id then delete src user.
        Returns dict of per-table (updated, deleted) counts."""
        totals = {}
        for table, column in fks:
            cur.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" = %s', (src_id,))
            n_src = cur.fetchone()[0]
            if not n_src:
                continue
            uniques = uniques_cache.setdefault(table, self._single_col_uniques(cur, table))
            if column in uniques:
                cur.execute(f'SELECT 1 FROM "{table}" WHERE "{column}" = %s LIMIT 1', (tgt_id,))
                if cur.fetchone():
                    cur.execute(f'DELETE FROM "{table}" WHERE "{column}" = %s', (src_id,))
                    totals[table] = (0, cur.rowcount)
                    continue
            cur.execute(
                f'UPDATE "{table}" SET "{column}" = %s WHERE "{column}" = %s',
                (tgt_id, src_id),
            )
            totals[table] = (cur.rowcount, 0)
        cur.execute(f'DELETE FROM "{USERS_TABLE}" WHERE id = %s', (src_id,))
        return totals

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options.get('limit')

        with connection.cursor() as cur:
            pairs, unmatched = self._discover_pairs(cur)
            fks = self._discover_fks(cur)

        self.stdout.write(self.style.NOTICE(
            f'Placeholders: {len(pairs) + len(unmatched)}  '
            f'Mergeable pairs: {len(pairs)}  '
            f'Unmatched: {len(unmatched)}  '
            f'FK columns to sweep: {len(fks)}'
        ))

        if limit is not None:
            pairs = pairs[:limit]
            self.stdout.write(self.style.WARNING(f'--limit {limit}: processing first {len(pairs)} only.'))

        if not pairs:
            self.stdout.write(self.style.SUCCESS('Nothing to merge.'))
            self._report_unmatched(unmatched)
            return

        if dry_run:
            self.stdout.write(self.style.WARNING('--dry-run: no DB changes.'))
            for (sid, suname, semail), (tid, tuname, temail, _), code in pairs[:20]:
                self.stdout.write(
                    f'  {sid:>4} {suname:32s} -> {tid:>4} {tuname:32s}  (emp_id={code})'
                )
            if len(pairs) > 20:
                self.stdout.write(f'  ... and {len(pairs) - 20} more.')
            self._report_unmatched(unmatched)
            return

        uniques_cache = {}
        succeeded = 0
        failed = []

        for (sid, suname, semail), (tid, tuname, temail, _), code in pairs:
            try:
                with transaction.atomic():
                    with connection.cursor() as cur:
                        totals = self._merge_one(cur, sid, tid, fks, uniques_cache)
                succeeded += 1
                summary = ', '.join(
                    f'{t}: upd={u},del={d}' for t, (u, d) in totals.items()
                ) or '(no FK rows)'
                self.stdout.write(
                    f'[{succeeded:>3}/{len(pairs)}] merged {sid} ({suname}) -> {tid} ({tuname})  '
                    f'code={code}  {summary}'
                )
            except Exception as exc:
                failed.append((sid, suname, str(exc)))
                self.stderr.write(self.style.ERROR(
                    f'  FAILED src={sid} ({suname}) -> tgt={tid}: {type(exc).__name__}: {exc}'
                ))

        self.stdout.write(self.style.SUCCESS(
            f'Done. Succeeded: {succeeded}/{len(pairs)}  Failed: {len(failed)}'
        ))
        if failed:
            self.stdout.write('Failures:')
            for sid, suname, msg in failed:
                self.stdout.write(f'  {sid} {suname}: {msg}')
        self._report_unmatched(unmatched)

    def _report_unmatched(self, unmatched):
        self.stdout.write('')
        self.stdout.write(self.style.NOTICE(
            f'Unmatched placeholders (left untouched — not duplicates): {len(unmatched)}'
        ))
        for pid, puname, pemail, reason in unmatched[:10]:
            self.stdout.write(f'  {pid} {puname} {pemail}  |  {reason}')
        if len(unmatched) > 10:
            self.stdout.write(f'  ... and {len(unmatched) - 10} more.')
