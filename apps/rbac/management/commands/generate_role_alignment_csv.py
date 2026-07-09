"""
generate_role_alignment_csv — produce a preview CSV of proposed role
assignments for all active RADAI users, using the Matrix biometric
department as the primary signal.

Nothing is written to the DB. The CSV is meant to be reviewed and edited
(rows can be corrected or removed) before feeding it into
``align_user_roles --csv <file> --apply``.

Output columns:
    email, name, current_roles, proposed_role, source, confidence, notes

Usage:
    python manage.py generate_role_alignment_csv --output proposed_alignment.csv

    # or in the backend container against production:
    docker exec -e DATABASE_URL="postgres://..." aiflow_backend_local \\
      python manage.py generate_role_alignment_csv --output /tmp/prod_alignment.csv
"""
from __future__ import annotations

import csv
import sys

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.db import connection

from apps.rbac.models import Role, UserProfile, UserRole
from apps.rbac.department_role_mapping import (
    DEFAULT_FALLBACK_ROLE,
    SKIP,
    resolve_role_for_department,
)

# Emails that must never be reassigned — the four active super-admins plus
# the two RBAC super_admin holders. Kept in sync with align_super_admins.
PROTECTED_EMAILS = {
    "tanzeem.agra@rejlers.ae",
    "jarmo.suominen@rejlers.ae",
    "darshna.chetwani@rejlers.ae",
    "debasis.sana@rejlers.ae",
    "moghawanmeh@rejlers.ae",
    "sanglin.samuel@rejlers.ae",
}

# The biometric mirror table name (soft-coded — currently
# ``timesheet_biometricusermaster``).
BIOMETRIC_TABLE = "timesheet_biometricusermaster"

# Filter for "real" user accounts. The biometric sync creates placeholder
# accounts like ``12345.0@rejlers.ae`` — those should be skipped.
NUMERIC_PLACEHOLDER_REGEX = r"^[0-9]+(\.[0-9]+)?@rejlers\.ae$"


User = get_user_model()


class Command(BaseCommand):
    help = (
        "Generate a CSV of proposed role assignments derived from the "
        "biometric department field. Read-only; safe to run any time."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output", required=True,
            help="Destination CSV path (written from inside the process; no stdout mixing).",
        )
        parser.add_argument(
            "--include-placeholders",
            action="store_true",
            default=False,
            help="Include numeric-email placeholder accounts (default: skip them).",
        )
        parser.add_argument(
            "--all-users",
            action="store_true",
            default=False,
            help="Include inactive users too (default: active only).",
        )

    def handle(self, *args, **options):
        output_path:          str  = options["output"]
        include_placeholders: bool = options["include_placeholders"]
        include_inactive:     bool = options["all_users"]

        # Verify the biometric table exists (mirror sync must have run)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT to_regclass(%s) IS NOT NULL", [f"public.{BIOMETRIC_TABLE}"]
            )
            has_table = cur.fetchone()[0]
        if not has_table:
            self.stderr.write(self.style.ERROR(
                f"Table '{BIOMETRIC_TABLE}' does not exist in this database. "
                "Run the timesheet_mirror_sync agent first."
            ))
            return

        # Pull biometric dept per email (both office_email and personal_email)
        biometric = {}
        with connection.cursor() as cur:
            cur.execute(f"""
                SELECT LOWER(office_email)   AS email, department, designation, full_name
                FROM {BIOMETRIC_TABLE}
                WHERE office_email IS NOT NULL AND office_email <> ''
                UNION ALL
                SELECT LOWER(personal_email) AS email, department, designation, full_name
                FROM {BIOMETRIC_TABLE}
                WHERE personal_email IS NOT NULL AND personal_email <> ''
            """)
            for email, dept, desig, name in cur.fetchall():
                # First non-blank department wins if the same email appears twice
                if email not in biometric or (dept and not biometric[email]["dept"]):
                    biometric[email] = {"dept": dept or "", "designation": desig or "", "name": name or ""}

        # Load users + their current primary role
        qs = User.objects.all().order_by("email")
        if not include_inactive:
            qs = qs.filter(is_active=True)
        if not include_placeholders:
            qs = qs.exclude(email__iregex=NUMERIC_PLACEHOLDER_REGEX)

        # Pre-fetch current roles per user
        current_roles_by_user = {}
        for ur in UserRole.objects.select_related("user_profile__user", "role"):
            uid = ur.user_profile.user_id
            current_roles_by_user.setdefault(uid, []).append(ur.role.code)

        # Emit CSV to the requested path (avoids stdout mixing with Django startup logs)
        try:
            csv_file = open(output_path, "w", encoding="utf-8", newline="")
        except OSError as exc:
            raise CommandError(f"Cannot open '{output_path}' for writing: {exc}")
        writer = csv.writer(csv_file, lineterminator="\n")
        writer.writerow([
            "email", "name", "current_roles", "proposed_role",
            "source", "confidence", "notes",
        ])

        stats = {
            "high": 0, "medium": 0, "none": 0,
            "skip": 0, "protected": 0, "no_profile": 0, "no_biometric": 0,
        }

        for user in qs.iterator():
            email_lc = user.email.lower()
            display_name = f"{user.first_name} {user.last_name}".strip()

            # Protected super-admins: never propose changes
            if email_lc in PROTECTED_EMAILS:
                writer.writerow([user.email, display_name,
                                 ",".join(current_roles_by_user.get(user.id, [])),
                                 "", "protected", "keep", "super-admin — do not modify"])
                stats["protected"] += 1
                continue

            # Must have a UserProfile to hold roles
            has_profile = UserProfile.objects.filter(user=user).exists()
            if not has_profile:
                writer.writerow([user.email, display_name, "",
                                 "", "no-profile", "none",
                                 "user has no RBAC UserProfile — create one first"])
                stats["no_profile"] += 1
                continue

            bio = biometric.get(email_lc)
            if not bio:
                # Try name match as a fallback (biometric full_name vs "first last")
                bio = None
                if display_name:
                    for record in biometric.values():
                        if record["name"] and record["name"].strip().lower() == display_name.lower():
                            bio = record
                            break

            if not bio:
                writer.writerow([user.email, display_name,
                                 ",".join(current_roles_by_user.get(user.id, [])),
                                 "", "no-biometric", "none",
                                 "no biometric record — manual assignment needed"])
                stats["no_biometric"] += 1
                continue

            role_code, confidence = resolve_role_for_department(bio["dept"])
            if role_code == SKIP:
                writer.writerow([user.email, display_name,
                                 ",".join(current_roles_by_user.get(user.id, [])),
                                 "", f"dept={bio['dept']}", "skip",
                                 "biometric marks this as external / non-Rejlers"])
                stats["skip"] += 1
                continue
            if role_code is None:
                writer.writerow([user.email, display_name,
                                 ",".join(current_roles_by_user.get(user.id, [])),
                                 "", f"dept={bio['dept']}", "none",
                                 "department not in mapping — manual assignment needed"])
                stats["none"] += 1
                continue

            notes = ""
            if role_code == DEFAULT_FALLBACK_ROLE:
                notes = "review — mapped to fallback role"

            writer.writerow([
                user.email, display_name,
                ",".join(current_roles_by_user.get(user.id, [])),
                role_code,
                f"dept={bio['dept']}",
                confidence,
                notes,
            ])
            stats[confidence] += 1

        # Print summary to STDERR so it doesn't corrupt the CSV
        csv_file.close()
        total = sum(stats.values())
        self.stderr.write("")
        self.stderr.write(self.style.HTTP_INFO("── generate_role_alignment_csv — summary ──"))
        self.stderr.write(f"  output file    {output_path}   ({total} rows)")
        for k in ("high", "medium", "none", "skip", "protected", "no_profile", "no_biometric"):
            self.stderr.write(f"  {k:<14} {stats[k]}")
        self.stderr.write(self.style.SUCCESS("Done."))
