"""
align_super_admins — one-shot cleanup of RADAI super-admin footprint.

Solves three problems observed in production:
  1. Dead / test / typo accounts holding either the Django ``is_superuser``
     flag or the RBAC ``super_admin`` role (or both).
  2. A phantom Level-1 role with empty ``code`` and 4 members (some of
     whom are already legitimate super-admins).
  3. Drift between Django super powers and RBAC super_admin role membership
     (people who have one but not the other).

The intent list is soft-coded at the top of the file — audit it before
running. Dry-run is the default; nothing writes without ``--apply``.

Usage:
    # Preview against whatever DB DATABASE_URL points at
    python manage.py align_super_admins

    # Apply (requires explicit confirmation of the operator's own email)
    python manage.py align_super_admins --apply --confirm-operator tanzeem.agra@rejlers.ae
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.rbac.models import (
    AuditLog,
    Role,
    UserProfile,
    UserRole,
)

User = get_user_model()

# ─────────────────────────────────────────────────────────────────────────────
# SOFT-CODED INTENT — audit these lists before running with --apply
# ─────────────────────────────────────────────────────────────────────────────

# The four humans who MUST retain full super-admin (Django + RBAC).
# Never demoted, never revoked, protected against typos in the other lists.
KEEP_SUPER_ADMIN = {
    "tanzeem.agra@rejlers.ae",       # Platform owner
    "jarmo.suominen@rejlers.ae",     # Rejlers senior admin
    "darshna.chetwani@rejlers.ae",   # Rejlers admin
    "debasis.sana@rejlers.ae",       # Rejlers admin
}

# Active people who currently hold the RBAC super_admin role but NOT the
# Django is_superuser flag — promote them to Django too so the two stay
# in sync (or move them into KEEP_SUPER_ADMIN if you want them staying).
PROMOTE_TO_DJANGO_SUPERUSER = {
    "moghawanmeh@rejlers.ae",        # Mohamad El-Ghawanmeh (active)
    "sanglin.samuel@rejlers.ae",     # Sanglin Samuel (active)
}

# Dead / test / typo / personal-email accounts. All flags stripped, then
# the account is deactivated (is_active=False) if it isn't already.
REVOKE_ALL = {
    "admin@rejlers.com",             # Default seed, inactive
    "info@rejlers.com",              # Default seed, inactive
    "test@test.com",                 # Test account
    "test@radai.ae",                 # Test account
    "demo.user@example.com",         # Demo account
    "tanzeem.agra@gmail.com",        # Personal Gmail (should not be admin)
    "tanzeem.agra@rejler.ae",        # Typo of tanzeem.agra@rejlers.ae
    "meera.alameri@rejlers.ae",      # Inactive
    "muhammed.ahamed@rejlers.ae",    # Inactive
    "shareeq@rejlers.ae",            # Inactive
}

# The phantom role (level=1, code='', name='Super Admin') is deleted after
# its UserRole membership rows are removed. If the role does not exist the
# step is a no-op.
PHANTOM_ROLE_FILTER = {"level": 1, "code": ""}


# ─────────────────────────────────────────────────────────────────────────────
class Command(BaseCommand):
    help = (
        "Align super-admin privileges: revoke dead/test/typo accounts, "
        "sync Django is_superuser with RBAC super_admin role, and delete "
        "the phantom Level-1 empty-code role. Dry-run by default."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            default=False,
            help="Actually write changes. Without this flag everything is preview-only.",
        )
        parser.add_argument(
            "--confirm-operator",
            type=str,
            default="",
            help=(
                "Operator's own email (required with --apply). Must be in "
                "KEEP_SUPER_ADMIN. Prevents accidental invocation."
            ),
        )

    # ── entry point ───────────────────────────────────────────────────────
    def handle(self, *args, **options):
        apply_changes: bool = options["apply"]
        confirm_operator: str = (options.get("confirm_operator") or "").strip().lower()

        if apply_changes:
            if not confirm_operator:
                raise CommandError(
                    "--apply requires --confirm-operator <your-email>. Aborting."
                )
            if confirm_operator not in {e.lower() for e in KEEP_SUPER_ADMIN}:
                raise CommandError(
                    f"Operator '{confirm_operator}' is not in KEEP_SUPER_ADMIN. "
                    f"Refusing to apply."
                )

        mode = "APPLY" if apply_changes else "DRY-RUN"
        self._hdr(f"align_super_admins — {mode}")

        try:
            super_admin_role = Role.objects.get(code="super_admin")
        except Role.DoesNotExist:
            raise CommandError(
                "RBAC role 'super_admin' does not exist. Run seed_rbac first."
            )

        plan = self._build_plan(super_admin_role)
        self._print_plan(plan)

        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                "\n[DRY-RUN] No changes written. Re-run with:\n"
                f"  --apply --confirm-operator <your-email>\n"
            ))
            return

        # ── APPLY ────────────────────────────────────────────────────────
        with transaction.atomic():
            self._apply(plan, super_admin_role, confirm_operator)

        self.stdout.write(self.style.SUCCESS("\n✅ Applied successfully."))
        self.stdout.write("Re-run the audit script to verify the final state.")

    # ── planning ──────────────────────────────────────────────────────────
    def _build_plan(self, super_admin_role: Role) -> dict:
        """Compute what would change without writing anything."""
        keep_lc    = {e.lower() for e in KEEP_SUPER_ADMIN}
        promote_lc = {e.lower() for e in PROMOTE_TO_DJANGO_SUPERUSER}
        revoke_lc  = {e.lower() for e in REVOKE_ALL}

        # Safety: KEEP wins over REVOKE if there's a conflict.
        revoke_lc -= keep_lc

        actions = {
            "revoke": [],           # [(user, has_django, has_rbac)]
            "promote_django": [],   # [user]
            "protect_keep": [],     # [user]  (informational)
            "phantom_role": None,   # {'role': Role, 'members': [User]}
            "warnings": [],
        }

        # Load candidate users case-insensitively
        all_emails = keep_lc | promote_lc | revoke_lc
        users_by_email = {
            u.email.lower(): u
            for u in User.objects.filter(email__iregex=r"^(" + "|".join(
                [e.replace(".", r"\.").replace("+", r"\+") for e in all_emails]
            ) + r")$")
        }

        rbac_holders = set(
            UserRole.objects.filter(role=super_admin_role)
            .values_list("user_profile__user__email", flat=True)
        )
        rbac_holders_lc = {e.lower() for e in rbac_holders if e}

        # -- REVOKE targets
        for email in sorted(revoke_lc):
            u = users_by_email.get(email)
            if not u:
                actions["warnings"].append(f"REVOKE target not found in DB: {email}")
                continue
            actions["revoke"].append({
                "user": u,
                "email": u.email,
                "has_django_super": bool(u.is_superuser),
                "has_rbac_super":   email in rbac_holders_lc,
                "is_active":        u.is_active,
            })

        # -- PROMOTE-to-Django targets
        for email in sorted(promote_lc):
            u = users_by_email.get(email)
            if not u:
                actions["warnings"].append(f"PROMOTE target not found in DB: {email}")
                continue
            if u.is_superuser:
                continue  # already correct
            actions["promote_django"].append(u)

        # -- KEEP list (informational)
        for email in sorted(keep_lc):
            u = users_by_email.get(email)
            if not u:
                actions["warnings"].append(f"KEEP target not found in DB: {email}")
                continue
            actions["protect_keep"].append(u)

        # -- Phantom role
        phantom = Role.objects.filter(**PHANTOM_ROLE_FILTER).first()
        if phantom:
            member_ids = UserRole.objects.filter(role=phantom).values_list(
                "user_profile__user_id", flat=True
            )
            members = list(User.objects.filter(id__in=member_ids))
            actions["phantom_role"] = {"role": phantom, "members": members}

        return actions

    # ── display ───────────────────────────────────────────────────────────
    def _print_plan(self, plan: dict) -> None:
        self._sub("KEEP super-admin (protected)")
        for u in plan["protect_keep"]:
            active = "active" if u.is_active else "INACTIVE"
            self.stdout.write(f"  ✓ {u.email:<40}  [{active}]  Django={u.is_superuser}")

        self._sub("PROMOTE to Django is_superuser (already RBAC super_admin)")
        if not plan["promote_django"]:
            self.stdout.write("  (none)")
        for u in plan["promote_django"]:
            self.stdout.write(f"  ↑ {u.email:<40}  {u.first_name} {u.last_name}")

        self._sub("REVOKE all super-admin flags + deactivate")
        if not plan["revoke"]:
            self.stdout.write("  (none)")
        for row in plan["revoke"]:
            flags = []
            if row["has_django_super"]: flags.append("Django")
            if row["has_rbac_super"]:   flags.append("RBAC")
            flag_str = "+".join(flags) or "(none)"
            act = "active" if row["is_active"] else "INACTIVE"
            self.stdout.write(
                f"  ✗ {row['email']:<40}  strip=[{flag_str}]  [{act}]"
            )

        self._sub("PHANTOM Level-1 empty-code role")
        ph = plan["phantom_role"]
        if not ph:
            self.stdout.write("  (no phantom role found)")
        else:
            r = ph["role"]
            self.stdout.write(f"  Role: id={r.id} name='{r.name}' code='{r.code}'")
            self.stdout.write(f"  Members ({len(ph['members'])}):")
            for u in ph["members"]:
                self.stdout.write(f"    - {u.email}  (has real super_admin? "
                                  f"{'yes' if u.email.lower() in {x.email.lower() for x in plan['protect_keep']} or u.is_superuser else 'no'})")

        if plan["warnings"]:
            self._sub("WARNINGS")
            for w in plan["warnings"]:
                self.stdout.write(self.style.WARNING(f"  ! {w}"))

    # ── execute ───────────────────────────────────────────────────────────
    def _apply(self, plan: dict, super_admin_role: Role, operator_email: str) -> None:
        """All writes happen inside a single transaction wrapped by caller."""
        operator = User.objects.filter(email__iexact=operator_email).first()
        now = timezone.now()

        # 1. PROMOTE to Django superuser
        for u in plan["promote_django"]:
            before = {"is_superuser": u.is_superuser, "is_staff": u.is_staff}
            u.is_superuser = True
            u.is_staff = True
            u.save(update_fields=["is_superuser", "is_staff"])
            self._audit(operator, "update", "User", u.id, u.email, before,
                        {"is_superuser": True, "is_staff": True}, reason="align_super_admins/promote")
            self.stdout.write(self.style.SUCCESS(f"  ↑ Promoted {u.email}"))

        # 2. REVOKE all super-admin flags from dead accounts
        for row in plan["revoke"]:
            u = row["user"]
            changes_before = {
                "is_superuser": u.is_superuser,
                "is_staff":     u.is_staff,
                "is_active":    u.is_active,
            }

            # Django flags
            if u.is_superuser or u.is_staff or u.is_active:
                u.is_superuser = False
                u.is_staff = False
                u.is_active = False
                u.save(update_fields=["is_superuser", "is_staff", "is_active"])

            # RBAC super_admin role
            revoked_rbac = 0
            profile = UserProfile.objects.filter(user=u).first()
            if profile:
                revoked_rbac, _ = UserRole.objects.filter(
                    user_profile=profile, role=super_admin_role,
                ).delete()

            self._audit(
                operator, "role_revoke", "User", u.id, u.email,
                changes_before,
                {"is_superuser": False, "is_staff": False, "is_active": False,
                 "rbac_super_admin_revoked": bool(revoked_rbac)},
                reason="align_super_admins/revoke",
            )
            self.stdout.write(self.style.SUCCESS(
                f"  ✗ Revoked {u.email}   (RBAC rows removed: {revoked_rbac or 0})"
            ))

        # 3. Phantom role — remove membership then delete
        ph = plan["phantom_role"]
        if ph:
            role = ph["role"]
            removed, _ = UserRole.objects.filter(role=role).delete()
            role_id = role.id
            role_repr = f"{role.name} (level={role.level}, code='{role.code}')"
            role.delete()
            self._audit(
                operator, "delete", "Role", role_id, role_repr,
                {"members": len(ph["members"])},
                {"deleted": True, "userrole_rows_removed": removed},
                reason="align_super_admins/phantom_role_cleanup",
            )
            self.stdout.write(self.style.SUCCESS(
                f"  🗑 Phantom role deleted (memberships removed: {removed})"
            ))

    # ── audit + formatting helpers ───────────────────────────────────────
    def _audit(self, operator, action, resource_type, resource_id, resource_repr,
               before, after, reason):
        try:
            AuditLog.objects.create(
                user=operator,
                user_email=(operator.email if operator else "system@radai"),
                action=action,
                resource_type=resource_type,
                resource_id=resource_id if isinstance(resource_id, (str, bytes)) or hasattr(resource_id, "hex") else None,
                resource_repr=str(resource_repr)[:255],
                changes={"before": self._jsonable(before), "after": self._jsonable(after)},
                metadata={"reason": reason, "command": "align_super_admins"},
                success=True,
            )
        except Exception as exc:  # never let audit failure abort the change
            self.stdout.write(self.style.WARNING(
                f"  (audit-log write failed: {exc.__class__.__name__}: {exc})"
            ))

    @staticmethod
    def _jsonable(d):
        out = {}
        for k, v in (d or {}).items():
            out[k] = v if isinstance(v, (str, int, float, bool, type(None))) else str(v)
        return out

    def _hdr(self, msg):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 72))
        self.stdout.write(self.style.MIGRATE_HEADING(msg))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 72))

    def _sub(self, msg):
        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO(f"── {msg} " + "─" * max(0, 60 - len(msg))))
