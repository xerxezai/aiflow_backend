"""
align_user_roles — bulk role assignment driven by a reviewed CSV.

The CSV shape matches the output of ``generate_role_alignment_csv``:

    email, name, current_roles, proposed_role, source, confidence, notes

Rows are processed as follows:
  * If ``proposed_role`` is blank → row is skipped.
  * If ``email`` is in ``PROTECTED_EMAILS`` → row is refused.
  * If ``proposed_role`` doesn't exist as an RBAC Role → row is refused
    (list of valid role codes is printed for convenience).
  * Otherwise → user's current non-super-admin, non-protected roles are
    replaced with a single UserRole pointing at ``proposed_role``. The
    user's ``super_admin`` role (if any) is preserved.

Dry-run is the default. ``--apply --confirm-operator <email>`` performs
the writes inside a single transaction and audit-logs every change with
``metadata.command = 'align_user_roles'``.

Usage:
    # 1) Generate preview
    python manage.py generate_role_alignment_csv > proposed_alignment.csv

    # 2) Review / edit proposed_alignment.csv in Excel

    # 3) Dry-run against the file
    python manage.py align_user_roles --csv proposed_alignment.csv

    # 4) Apply
    python manage.py align_user_roles --csv proposed_alignment.csv \\
        --apply --confirm-operator tanzeem.agra@rejlers.ae

    # Optional flags:
    #   --delete-orphan-custom   also delete custom_<user> roles after moving
    #                             the user off them (uses cleanup_custom_roles)
"""
from __future__ import annotations

import csv
from collections import defaultdict

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.rbac.models import AuditLog, Role, UserProfile, UserRole

User = get_user_model()

# Kept in sync with align_super_admins / generate_role_alignment_csv
PROTECTED_EMAILS = {
    "tanzeem.agra@rejlers.ae",
    "jarmo.suominen@rejlers.ae",
    "darshna.chetwani@rejlers.ae",
    "debasis.sana@rejlers.ae",
    "moghawanmeh@rejlers.ae",
    "sanglin.samuel@rejlers.ae",
}

# Roles we never touch on a user, even during a reassignment.
NEVER_REVOKE_ROLES = {"super_admin"}


class Command(BaseCommand):
    help = (
        "Apply a reviewed CSV of proposed role assignments. Replaces each "
        "user's non-super-admin roles with the single proposed role."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv", required=True, help="Path to the reviewed alignment CSV.",
        )
        parser.add_argument(
            "--apply", action="store_true", default=False,
            help="Apply changes. Default is dry-run.",
        )
        parser.add_argument(
            "--confirm-operator", type=str, default="",
            help=(
                "Operator email (required with --apply). Must be in "
                "PROTECTED_EMAILS. Prevents accidental invocation."
            ),
        )
        parser.add_argument(
            "--delete-orphan-custom", action="store_true", default=False,
            help=(
                "After reassignment, delete any custom_<user> Role that has "
                "no remaining members. Idempotent."
            ),
        )

    def handle(self, *args, **options):
        csv_path: str = options["csv"]
        apply_changes: bool = options["apply"]
        confirm_operator: str = (options.get("confirm_operator") or "").strip().lower()
        delete_orphan: bool = options["delete_orphan_custom"]

        if apply_changes:
            if not confirm_operator:
                raise CommandError(
                    "--apply requires --confirm-operator <your-email>."
                )
            if confirm_operator not in {e.lower() for e in PROTECTED_EMAILS}:
                raise CommandError(
                    f"Operator '{confirm_operator}' is not in PROTECTED_EMAILS. Refusing."
                )

        # Load valid role code set once
        valid_roles = {r.code: r for r in Role.objects.all()}

        # Parse CSV
        rows = []
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            missing = {"email", "proposed_role"} - set(reader.fieldnames or [])
            if missing:
                raise CommandError(
                    f"CSV missing required columns: {missing}. Got: {reader.fieldnames}"
                )
            for lineno, row in enumerate(reader, start=2):
                row["_lineno"] = lineno
                rows.append(row)

        plan = self._build_plan(rows, valid_roles)
        self._print_plan(plan, valid_roles)

        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                "\n[DRY-RUN] No changes written. Re-run with:\n"
                "  --apply --confirm-operator <your-email>\n"
            ))
            return

        with transaction.atomic():
            self._apply(plan, valid_roles, confirm_operator, delete_orphan)

        self.stdout.write(self.style.SUCCESS("\n✅ Applied successfully."))

    # ── planning ──────────────────────────────────────────────────────────
    def _build_plan(self, rows, valid_roles):
        plan = {
            "assign":    [],   # (user, target_role, roles_to_revoke, notes)
            "skip":      [],   # (email, reason)
            "refused":   [],   # (email, reason)
        }
        emails_needed = [r["email"].strip() for r in rows if r.get("email")]
        users_by_email = {
            u.email.lower(): u
            for u in User.objects.filter(email__in=emails_needed)
        }
        # Also try case-insensitive lookup
        for u in User.objects.filter(email__iregex="^(" + "|".join(
            [e.replace(".", r"\.") for e in emails_needed if e]
        ) + ")$"):
            users_by_email.setdefault(u.email.lower(), u)

        # Existing UserRole set per user
        current = defaultdict(list)
        for ur in UserRole.objects.select_related("user_profile__user", "role"):
            current[ur.user_profile.user_id].append(ur)

        for row in rows:
            email = (row.get("email") or "").strip()
            target = (row.get("proposed_role") or "").strip()
            lineno = row.get("_lineno", "?")

            if not email:
                plan["skip"].append((f"line {lineno}", "blank email"))
                continue
            if not target:
                plan["skip"].append((email, "blank proposed_role"))
                continue

            if email.lower() in PROTECTED_EMAILS:
                plan["refused"].append((email, "protected super-admin — refusing to modify"))
                continue

            if target not in valid_roles:
                plan["refused"].append((email, f"unknown role code '{target}'"))
                continue

            user = users_by_email.get(email.lower())
            if not user:
                plan["refused"].append((email, "no matching User in database"))
                continue

            profile = UserProfile.objects.filter(user=user).first()
            if not profile:
                plan["refused"].append((email, "user has no RBAC UserProfile"))
                continue

            existing = current.get(user.id, [])
            already_correct = any(
                ur.role.code == target and ur.is_primary for ur in existing
            )

            # Roles to revoke = everything except super_admin and the target itself
            to_revoke = [
                ur for ur in existing
                if ur.role.code not in NEVER_REVOKE_ROLES
                and ur.role.code != target
            ]

            plan["assign"].append({
                "user": user,
                "profile": profile,
                "target_role_code": target,
                "target_role": valid_roles[target],
                "to_revoke": to_revoke,
                "already_correct": already_correct,
                "existing_codes": [ur.role.code for ur in existing],
                "notes": row.get("notes", ""),
            })
        return plan

    # ── display ───────────────────────────────────────────────────────────
    def _print_plan(self, plan, valid_roles):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 72))
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"align_user_roles — {len(plan['assign'])} assignments, "
            f"{len(plan['skip'])} skipped, {len(plan['refused'])} refused"
        ))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 72))

        # Group by target role for a compact summary
        by_role = defaultdict(int)
        no_change = 0
        for item in plan["assign"]:
            if item["already_correct"] and not item["to_revoke"]:
                no_change += 1
                continue
            by_role[item["target_role_code"]] += 1

        self.stdout.write("\nProposed distribution (changes only):")
        for role_code, n in sorted(by_role.items(), key=lambda kv: -kv[1]):
            self.stdout.write(f"  {role_code:<28} {n:>4} users")
        if no_change:
            self.stdout.write(f"  (no change needed)          {no_change:>4} users")

        if plan["refused"]:
            self.stdout.write(self.style.WARNING("\nRefused rows:"))
            for email, reason in plan["refused"][:30]:
                self.stdout.write(f"  ✗ {email:<40} {reason}")
            if len(plan["refused"]) > 30:
                self.stdout.write(f"  ... {len(plan['refused']) - 30} more refused")

        if plan["skip"]:
            self.stdout.write(f"\nSkipped rows: {len(plan['skip'])}")

    # ── execute ───────────────────────────────────────────────────────────
    def _apply(self, plan, valid_roles, operator_email, delete_orphan):
        operator = User.objects.filter(email__iexact=operator_email).first()
        assigned = revoked = deleted_custom = 0

        for item in plan["assign"]:
            user     = item["user"]
            profile  = item["profile"]
            role     = item["target_role"]
            existing = item["existing_codes"]

            # Revoke old roles (except super_admin, except target)
            revoked_codes = []
            for ur in item["to_revoke"]:
                code = ur.role.code
                ur.delete()
                revoked_codes.append(code)
                revoked += 1

            # Assign target role as primary (idempotent — get_or_create)
            ur, created = UserRole.objects.get_or_create(
                user_profile=profile, role=role,
                defaults={"is_primary": True, "assigned_by": operator},
            )
            if not created and not ur.is_primary:
                ur.is_primary = True
                ur.save(update_fields=["is_primary"])
            if created:
                assigned += 1

            self._audit(
                operator, "role_assign", "User", user.id, user.email,
                {"existing_roles": existing},
                {"target_role": role.code, "revoked": revoked_codes,
                 "created_new_userrole": created},
                reason="align_user_roles",
            )

        # Optional: delete now-empty custom_<name> roles
        if delete_orphan:
            from apps.rbac.rbac_config import MODULE_ASSIGNMENT_CONFIG
            prefix = MODULE_ASSIGNMENT_CONFIG.get("custom_role_prefix", "custom_")
            orphans = Role.objects.filter(code__startswith=prefix).exclude(
                id__in=UserRole.objects.values_list("role_id", flat=True)
            )
            for role in orphans:
                role_id = role.id
                role_repr = f"{role.name} (code={role.code})"
                role.delete()
                deleted_custom += 1
                self._audit(
                    operator, "delete", "Role", role_id, role_repr,
                    {}, {"deleted": True, "reason": "orphaned after align_user_roles"},
                    reason="align_user_roles/cleanup_custom",
                )

        self.stdout.write(self.style.SUCCESS(
            f"\n  Assignments created: {assigned}\n"
            f"  Roles revoked:       {revoked}\n"
            f"  Custom roles pruned: {deleted_custom}"
        ))

    # ── audit helper ─────────────────────────────────────────────────────
    def _audit(self, operator, action, resource_type, resource_id, resource_repr,
               before, after, reason):
        try:
            AuditLog.objects.create(
                user=operator,
                user_email=(operator.email if operator else "system@radai"),
                action=action,
                resource_type=resource_type,
                resource_id=resource_id if hasattr(resource_id, "hex") else None,
                resource_repr=str(resource_repr)[:255],
                changes={"before": self._jsonable(before),
                         "after":  self._jsonable(after)},
                metadata={"reason": reason, "command": "align_user_roles"},
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
            if isinstance(v, list):
                out[k] = [str(x) for x in v]
            elif isinstance(v, (str, int, float, bool, type(None))):
                out[k] = v
            else:
                out[k] = str(v)
        return out
