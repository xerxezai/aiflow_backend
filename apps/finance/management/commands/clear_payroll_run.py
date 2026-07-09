"""
Django management command to clear a specific payroll run
Usage: docker exec -it aiflow_backend_local python manage.py clear_payroll_run --run-code PAY-APR-2026
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from apps.finance.salary_models import PayrollRun, SalarySlip


class Command(BaseCommand):
    help = 'Clear a specific payroll run and its salary slips'

    def add_arguments(self, parser):
        parser.add_argument(
            '--run-code',
            type=str,
            required=True,
            help='Payroll run code to delete (e.g., PAY-APR-2026)'
        )

    @transaction.atomic
    def handle(self, *args, **options):
        run_code = options['run_code']
        
        try:
            payroll_run = PayrollRun.objects.get(run_code=run_code)
        except PayrollRun.DoesNotExist:
            self.stdout.write(self.style.WARNING(f'Payroll run {run_code} not found'))
            return
        
        # Count slips
        slip_count = SalarySlip.objects.filter(payroll_run=payroll_run).count()
        
        self.stdout.write(f'Found payroll run: {run_code}')
        self.stdout.write(f'  - Month/Year: {payroll_run.month}/{payroll_run.year}')
        self.stdout.write(f'  - Status: {payroll_run.status}')
        self.stdout.write(f'  - Salary slips: {slip_count}')
        
        # Delete slips first
        self.stdout.write('\nDeleting salary slips...')
        deleted_slips = SalarySlip.objects.filter(payroll_run=payroll_run).delete()
        self.stdout.write(self.style.SUCCESS(f'✓ Deleted {deleted_slips[0]} salary slips'))
        
        # Delete payroll run
        self.stdout.write('\nDeleting payroll run...')
        payroll_run.delete()
        self.stdout.write(self.style.SUCCESS(f'✓ Deleted payroll run {run_code}'))
        
        self.stdout.write(self.style.SUCCESS('\n✓ Cleanup complete!'))
