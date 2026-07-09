"""
cleanup_dead_user_accounts — delete users that were auto-created as biometric
placeholders and have never been used (no RBAC profile, never logged in).

Also merges a specific set of case-variant duplicate emails into a single row.

Discovery (2026-07-01) showed the production DB has 762 users but only 358
have an rbac_user_profiles row. The other 403 are auto-created stubs that
break onboarding tooling and confuse the alignment scripts. They have only
two FK dependents:
  - notifications.recipient_id             (welcome message)
  - notification_preferences.user_id       (default prefs row)

Both are safe to cascade-delete: these users never logged in, never received
a real message, and their preferences are just defaults.

Usage:
    # dry-run (default)
    python manage.py cleanup_dead_user_accounts

    # apply (against prod)
    docker exec -e DATABASE_URL="postgresql://..." aiflow_backend_local \\
      python manage.py cleanup_dead_user_accounts \\
        --apply --confirm-operator tanzeem.agra@rejlers.ae
"""
from __future__ import annotations

from collections import defaultdict

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.rbac.models import AuditLog

User = get_user_model()

# Same protection list used by align_super_admins / align_user_roles
PROTECTED_EMAILS = {
    "tanzeem.agra@rejlers.ae",
    "jarmo.suominen@rejlers.ae",
    "darshna.chetwani@rejlers.ae",
    "debasis.sana@rejlers.ae",
    "moghawanmeh@rejlers.ae",
    "sanglin.samuel@rejlers.ae",
}

# Additional pairs known to be case-variant duplicates. Both point at the
# same real person; the value picks which one to keep.
# Left = canonical (kept). Right = duplicate (deleted, refs re-pointed).
DUPLICATE_EMAIL_MERGES = {
    "yahya.mubarak@rejlers.ae": "Yahya.Mubarak@rejlers.ae",
    # Add more pairs here as they are discovered
}

# Additional FK dependents that must be cleared before DELETE FROM users.
# Discovered by scripts/check_dead_user_refs.sql on 2026-07-01.
DEPENDENT_TABLES_TO_CLEAR = [
    ("notifications",           "recipient_id"),
    ("notification_preferences", "user_id"),
]

# Grand-children of the dependent tables above. Cleared before the dependents.
# Format: (child_table, child_fk_col, parent_table, parent_user_col)
# Meaning: DELETE FROM <child_table> WHERE <child_fk_col> IN
#          (SELECT id FROM <parent_table> WHERE <parent_user_col> = ANY(dead_ids))
CASCADE_GRANDCHILDREN = [
    ("notification_logs", "notification_id", "notifications", "recipient_id"),
]


class Command(BaseCommand):
    help = (
        "Delete biometric-stub user accounts that have no RBAC profile "
        "and no meaningful references. Also merges case-variant duplicate emails."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true", default=False,
            help="Perform deletes. Default is dry-run.",
        )
        parser.add_argument(
            "--confirm-operator", type=str, default="",
            help="Operator email (required with --apply). Must be in PROTECTED_EMAILS.",
        )
        parser.add_argument(
            "--skip-merges", action="store_true", default=False,
            help="Skip case-variant merges (useful for local re-sync when IDs are inverted).",
        )

    def handle(self, *args, **options):
        apply_changes: bool = options["apply"]
        confirm_operator: str = (options.get("confirm_operator") or "").strip().lower()
        skip_merges: bool = options.get("skip_merges", False)

        if apply_changes:
            if not confirm_operator:
                raise CommandError("--apply requires --confirm-operator <your-email>.")
            if confirm_operator not in {e.lower() for e in PROTECTED_EMAILS}:
                raise CommandError(
                    f"Operator '{confirm_operator}' is not in PROTECTED_EMAILS. Refusing."
                )

        plan = self._build_plan(skip_merges=skip_merges)
        self._print_plan(plan)

        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                "\n[DRY-RUN] Nothing deleted. Re-run with:\n"
                "  --apply --confirm-operator <your-email>\n"
            ))
            return

        with transaction.atomic():
            self._apply(plan, confirm_operator)
        self.stdout.write(self.style.SUCCESS("\n✅ Cleanup complete."))

    # ── planning ──────────────────────────────────────────────────────────
    def _build_plan(self, skip_merges: bool = False):
        """Return the set of user ids to delete + merge pairs."""
        # Dead users = no rbac_user_profiles row, never logged in, and not protected
        with connection.cursor() as cur:
            cur.execute("""
                SELECT u.id, u.email, u.is_active, u.last_login IS NOT NULL AS has_logged_in
                FROM users u
                LEFT JOIN rbac_user_profiles p ON p.user_id = u.id
                WHERE p.id IS NULL
                ORDER BY u.email
            """)
            dead_candidates = cur.fetchall()

        protected_lower = {e.lower() for e in PROTECTED_EMAILS}
        dead_users = []
        skipped_protected = []
        skipped_active = []

        for uid, email, is_active, has_logged_in in dead_candidates:
            if email.lower() in protected_lower:
                skipped_protected.append((uid, email))
                continue
            if has_logged_in:
                skipped_active.append((uid, email))
                continue
            dead_users.append((uid, email))

        # Merge pairs: only include if BOTH sides exist
        merges = []
        if not skip_merges:
            for keep_email, drop_email in DUPLICATE_EMAIL_MERGES.items():
                keep = User.objects.filter(email=keep_email).first()
                drop = User.objects.filter(email=drop_email).first()
                if keep and drop and keep.id != drop.id:
                    merges.append({"keep": keep, "drop": drop})

        # Count FK references we'll clear per table
        dep_counts = {}
        if dead_users:
            ids_list = [uid for uid, _ in dead_users]
            with connection.cursor() as cur:
                for table, col in DEPENDENT_TABLES_TO_CLEAR:
                    cur.execute(
                        f'SELECT COUNT(*) FROM "{table}" WHERE "{col}" = ANY(%s)',
                        [ids_list],
                    )
                    dep_counts[(table, col)] = cur.fetchone()[0]

        # Bucket dead users by email pattern for reporting
        buckets = defaultdict(int)
        for _, email in dead_users:
            if "@radai.ae" in email.lower():
                buckets["radai_stub"] += 1
            elif email[0:1].isdigit():
                buckets["numeric_stub"] += 1
            else:
                buckets["other"] += 1

        return {
            "dead_users":         dead_users,
            "skipped_protected":  skipped_protected,
            "skipped_active":     skipped_active,
            "merges":             merges,
            "dep_counts":         dep_counts,
            "buckets":            dict(buckets),
        }

    # ── display ───────────────────────────────────────────────────────────
    def _print_plan(self, plan):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 72))
        self.stdout.write(self.style.MIGRATE_HEADING(
            "cleanup_dead_user_accounts"
        ))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 72))

        self.stdout.write(f"\nDead users to delete: {len(plan['dead_users'])}")
        for bucket, n in sorted(plan["buckets"].items()):
            self.stdout.write(f"  {bucket:<20} {n:>4}")

        self.stdout.write("\nDependent rows to clear:")
        for (t, c), n in plan["dep_counts"].items():
            self.stdout.write(f"  {t}.{c:<20} {n:>4}")

        self.stdout.write(f"\nCase-variant merges: {len(plan['merges'])}")
        for m in plan["merges"]:
            self.stdout.write(
                f"  KEEP  id={m['keep'].id} {m['keep'].email}\n"
                f"  DROP  id={m['drop'].id} {m['drop'].email}"
            )

        if plan["skipped_protected"]:
            self.stdout.write(f"\nSkipped (protected): {len(plan['skipped_protected'])}")
        if plan["skipped_active"]:
            self.stdout.write(f"Skipped (logged in): {len(plan['skipped_active'])}")

    # ── execute ───────────────────────────────────────────────────────────
    def _apply(self, plan, operator_email):
        operator = User.objects.filter(email__iexact=operator_email).first()

        dead_ids = [uid for uid, _ in plan["dead_users"]]

        # Phase 0 — clear grand-children (rows in child tables whose parent
        # rows will be deleted in Phase 1)
        with connection.cursor() as cur:
            for child_t, child_col, parent_t, parent_user_col in CASCADE_GRANDCHILDREN:
                cur.execute(
                    f'DELETE FROM "{child_t}" '
                    f'WHERE "{child_col}" IN '
                    f'(SELECT id FROM "{parent_t}" WHERE "{parent_user_col}" = ANY(%s))',
                    [dead_ids],
                )
                self.stdout.write(
                    f"  cleared {cur.rowcount:>5} rows from {child_t}.{child_col}"
                )

        # Phase 1 — clear dependent rows for dead users
        with connection.cursor() as cur:
            for table, col in DEPENDENT_TABLES_TO_CLEAR:
                cur.execute(
                    f'DELETE FROM "{table}" WHERE "{col}" = ANY(%s)',
                    [dead_ids],
                )
                self.stdout.write(
                    f"  cleared {cur.rowcount:>5} rows from {table}.{col}"
                )

        # Phase 2 — delete the dead users
        if dead_ids:
            with connection.cursor() as cur:
                cur.execute("DELETE FROM users WHERE id = ANY(%s)", [dead_ids])
                self.stdout.write(f"  deleted {cur.rowcount:>5} dead users")

        # Audit the bulk delete
        self._audit(
            operator, "delete", "User", None,
            f"{len(dead_ids)} dead accounts",
            before={"count": len(dead_ids)},
            after={"deleted": True, "buckets": plan["buckets"]},
            reason="cleanup_dead_user_accounts/bulk_delete",
        )

        # Phase 3 — merge case-variant duplicates
        for m in plan["merges"]:
            keep, drop = m["keep"], m["drop"]
            self._merge_user(keep, drop, operator)

    def _merge_user(self, keep, drop, operator):
        """Repoint all FK refs from `drop` to `keep`, then delete `drop`.

        Strategy for case-variant duplicates:
          0. Pre-strip drop-side RBAC data (profile + user_roles). This is
             safe because the drop side was created by biometric sync and
             the "real" account is `keep`.
          1. For every remaining FK-to-users column, UPDATE drop_id -> keep_id
             inside a SAVEPOINT so any UNIQUE-constraint collision only
             rolls back that one attempt (and we delete the drop-side row
             for that column instead).
          2. DELETE FROM users WHERE id = drop_id.
        """
        drop_id, keep_id = drop.id, keep.id

        # Phase 0 — strip drop-side RBAC data that would collide with keep
        with connection.cursor() as cur:
            cur.execute("""
                DELETE FROM rbac_user_roles
                WHERE user_profile_id IN (
                    SELECT id FROM rbac_user_profiles WHERE user_id = %s
                )
            """, [drop_id])
            roles_removed = cur.rowcount
            cur.execute("DELETE FROM rbac_user_profiles WHERE user_id = %s", [drop_id])
            profiles_removed = cur.rowcount
            self.stdout.write(
                f"  merge-strip: removed {roles_removed} user_roles, "
                f"{profiles_removed} user_profile for drop-side"
            )

        # Discover FK columns targeting users
        with connection.cursor() as cur:
            cur.execute("""
                SELECT tc.table_name, kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                     ON tc.constraint_name = kcu.constraint_name
                JOIN information_schema.constraint_column_usage ccu
                     ON tc.constraint_name = ccu.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND ccu.table_name = 'users'
                  AND tc.table_schema = 'public'
            """)
            fks = cur.fetchall()

        total_repointed = 0
        total_conflict_deleted = 0
        for table, col in fks:
            try:
                with transaction.atomic():   # savepoint
                    with connection.cursor() as cur:
                        cur.execute(
                            f'UPDATE "{table}" SET "{col}" = %s WHERE "{col}" = %s',
                            [keep_id, drop_id],
                        )
                        if cur.rowcount:
                            total_repointed += cur.rowcount
            except Exception as exc:
                # UNIQUE collision → drop the conflicting rows on the drop side
                self.stdout.write(self.style.WARNING(
                    f"  conflict on {table}.{col}: {exc.__class__.__name__} — "
                    f"deleting drop-side rows instead"
                ))
                try:
                    with transaction.atomic():
                        with connection.cursor() as cur:
                            cur.execute(
                                f'DELETE FROM "{table}" WHERE "{col}" = %s',
                                [drop_id],
                            )
                            total_conflict_deleted += cur.rowcount
                except Exception as exc2:
                    self.stdout.write(self.style.ERROR(
                        f"  ALSO failed to delete drop-side on {table}.{col}: "
                        f"{exc2.__class__.__name__}: {exc2}"
                    ))
                    raise

        with connection.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", [drop_id])
        self.stdout.write(
            f"  merged  {drop.email:<40} -> {keep.email} "
            f"(re-pointed {total_repointed}, conflict-deleted {total_conflict_deleted})"
        )

        self._audit(
            operator, "delete", "User", None,
            f"{drop.email} → {keep.email}",
            before={"drop_id": drop_id, "drop_email": drop.email},
            after={"kept_id": keep_id, "kept_email": keep.email},
            reason="cleanup_dead_user_accounts/merge",
        )

    # ── audit helper ─────────────────────────────────────────────────────
    def _audit(self, operator, action, resource_type, resource_id, resource_repr,
               before, after, reason):
        try:
            AuditLog.objects.create(
                user=operator,
                user_email=(operator.email if operator else "system@radai"),
                action=action,
                resource_type=resource_type,
                resource_id=None,   # bigint id, but resource_id is uuid — omit
                resource_repr=str(resource_repr)[:255],
                changes={"before": self._jsonable(before),
                         "after":  self._jsonable(after)},
                metadata={"reason": reason, "command": "cleanup_dead_user_accounts"},
                success=True,
            )
        except Exception as exc:
            self.stdout.write(self.style.WARNING(
                f"  (audit-log write failed: {exc.__class__.__name__}: {exc})"
            ))

    @staticmethod
    def _jsonable(d):
        out = {}
        for k, v in (d or {}).items():
            if isinstance(v, (str, int, float, bool, type(None))):
                out[k] = v
            elif isinstance(v, dict):
                out[k] = {kk: str(vv) for kk, vv in v.items()}
            elif isinstance(v, list):
                out[k] = [str(x) for x in v]
            else:
                out[k] = str(v)
        return out
