"""Validate procurement database integrity before pre-production promotion."""

import re

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.exceptions import InconsistentMigrationHistory

from apps.procurement.models import PurchaseOrder
from apps.procurement.services.purchase_order_numbering import PurchaseOrderNumberService


CANONICAL_PO = re.compile(r"^RAD-(GEN|PRJ)-PUR-\d{4}_\d{4}$")


class Command(BaseCommand):
    help = "Check database target, migrations, PO naming, and PR-to-PO integrity."

    def add_arguments(self, parser):
        parser.add_argument("--strict", action="store_true", help="Return a non-zero status when any blocker is found.")
        parser.add_argument("--expected-environment", help="Require settings.ENVIRONMENT to match this value.")

    def handle(self, *args, **options):
        db = connection.settings_dict
        environment = str(getattr(settings, "ENVIRONMENT", "unknown")).lower()
        blockers = []
        warnings = []

        expected_environment = (options.get("expected_environment") or "").lower()
        if expected_environment and environment != expected_environment:
            blockers.append(f"Environment is {environment}, expected {expected_environment}.")

        executor = MigrationExecutor(connection)
        try:
            executor.loader.check_consistent_history(connection)
        except InconsistentMigrationHistory as exc:
            blockers.append(f"Migration history is inconsistent: {exc}")
        unapplied = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if unapplied:
            blockers.append(f"{len(unapplied)} database migration(s) are not applied.")

        orders = list(PurchaseOrder.objects.select_related("pr_reference", "vendor"))
        canonical = [po for po in orders if CANONICAL_PO.fullmatch(po.po_number or "")]
        invalid_numbers = [po for po in orders if str(po.po_number or "").startswith("RAD-") and po not in canonical]
        missing_pr = [po for po in canonical if not po.pr_reference_id]
        missing_vendor = [po for po in canonical if not po.vendor_id]
        verification_failures = []
        reverse_mismatches = []
        authoritative = 0

        for po in canonical:
            pr_number = po.pr_reference.pr_number if po.pr_reference_id else None
            valid, message = PurchaseOrderNumberService.verify(po.po_number, pr_number)
            if not valid:
                verification_failures.append((po.po_number, message))
            if po.pr_reference_id and po.pr_reference.po_number_reference != po.po_number:
                reverse_mismatches.append(po.po_number)
            if any(
                isinstance(item, dict)
                and item.get("type") == "po_excel_import_source"
                and item.get("source_authority") == "Procurement Department"
                for item in (po.attachments or [])
            ):
                authoritative += 1

        if invalid_numbers:
            warnings.append(f"{len(invalid_numbers)} legacy RAD PO number(s) remain outside the canonical format.")
        if missing_pr:
            blockers.append(f"{len(missing_pr)} canonical PO(s) have no PR foreign key.")
        if missing_vendor:
            blockers.append(f"{len(missing_vendor)} canonical PO(s) have no vendor foreign key.")
        if verification_failures:
            blockers.append(f"{len(verification_failures)} canonical PO(s) fail scope/year validation.")
        if reverse_mismatches:
            blockers.append(f"{len(reverse_mismatches)} linked PR record(s) reference a different PO number.")

        self.stdout.write(f"Environment: {environment}")
        self.stdout.write(f"Database: {db.get('HOST')}:{db.get('PORT')}/{db.get('NAME')}")
        self.stdout.write(f"Purchase orders: {len(orders)} total; {len(canonical)} canonical")
        self.stdout.write(f"Canonical PR links: {len(canonical) - len(missing_pr)}/{len(canonical)}")
        self.stdout.write(f"Authoritative Excel audit records: {authoritative}")
        for warning in warnings:
            self.stdout.write(self.style.WARNING(f"WARNING: {warning}"))
        for blocker in blockers:
            self.stdout.write(self.style.ERROR(f"BLOCKER: {blocker}"))

        if blockers and options["strict"]:
            raise CommandError(f"Procurement readiness failed with {len(blockers)} blocker(s).")
        if blockers:
            self.stdout.write(self.style.WARNING("Procurement readiness: NOT READY"))
        else:
            self.stdout.write(self.style.SUCCESS("Procurement readiness: READY"))
