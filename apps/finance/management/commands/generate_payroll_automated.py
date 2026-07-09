"""
Django management command to manually trigger automated payroll generation
Usage:
    # Generate for current month
    python manage.py generate_payroll_automated
    
    # Generate for specific month (PRODUCTION)
    python manage.py generate_payroll_automated --year 2026 --month 6 --production
    
    # Force generation (skip validation gates)
    python manage.py generate_payroll_automated --force
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.finance.payroll_automation import auto_generate_monthly_payroll


class Command(BaseCommand):
    help = 'Manually trigger automated payroll generation'

    def add_arguments(self, parser):
        parser.add_argument(
            '--year',
            type=int,
            default=None,
            help='Target year (default: current year)'
        )
        parser.add_argument(
            '--month',
            type=int,
            default=None,
            help='Target month (default: current month)'
        )
        parser.add_argument(
            '--production',
            action='store_true',
            help='Run in production mode (connects to Railway DB)'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Skip validation gates (use with caution!)'
        )
        parser.add_argument(
            '--async',
            action='store_true',
            dest='run_async',
            help='Run as Celery task (async) instead of synchronously'
        )

    def handle(self, *args, **options):
        year = options['year']
        month = options['month']
        force = options['force']
        run_async = options['run_async']
        is_production = options['production']
        
        # Determine target period
        now = timezone.now()
        target_year = year or now.year
        target_month = month or now.month
        
        # Display banner
        self.stdout.write('=' * 80)
        self.stdout.write(self.style.SUCCESS('🤖 AUTOMATED PAYROLL GENERATION'))
        self.stdout.write('=' * 80)
        self.stdout.write(f'\n📅 Target Period:  {target_year}-{target_month:02d}')
        
        if is_production:
            self.stdout.write(self.style.ERROR('🎯 Mode:          PRODUCTION'))
            self.stdout.write(self.style.WARNING('⚠️  WARNING: Running in production mode!'))
        else:
            self.stdout.write(self.style.WARNING('🎯 Mode:          LOCAL'))
        
        if force:
            self.stdout.write(self.style.ERROR('⚠️  FORCE MODE:    Validation gates DISABLED'))
        
        if run_async:
            self.stdout.write('🔄 Execution:     Async (Celery task)')
        else:
            self.stdout.write('⏱️  Execution:     Synchronous')
        
        self.stdout.write('')
        
        # Confirmation prompt for production
        if is_production and not force:
            confirm = input('Are you sure you want to generate payroll in PRODUCTION? (yes/no): ')
            if confirm.lower() != 'yes':
                self.stdout.write(self.style.WARNING('\n❌ Operation cancelled'))
                return
        
        # Run generation
        self.stdout.write('🚀 Starting payroll generation...\n')
        
        try:
            if run_async:
                # Run as Celery task
                result = auto_generate_monthly_payroll.apply_async(
                    args=[target_year, target_month, force]
                )
                self.stdout.write(self.style.SUCCESS(
                    f'✅ Task queued: {result.id}'
                ))
                self.stdout.write('   Check Celery worker logs for progress')
            else:
                # Run synchronously
                result = auto_generate_monthly_payroll(target_year, target_month, force)
                
                # Display results
                self.stdout.write('\n' + '=' * 80)
                self.stdout.write(self.style.SUCCESS('📊 GENERATION RESULT'))
                self.stdout.write('=' * 80)
                self.stdout.write(f'\nStatus: {result["status"]}')
                
                if result['status'] == 'success':
                    stats = result['stats']
                    self.stdout.write(self.style.SUCCESS(
                        f'\n✅ Payroll run created: {result["run_code"]}'
                    ))
                    self.stdout.write(f'\n   Run ID:          {result["run_id"]}')
                    self.stdout.write(f'   Slips Created:   {stats["created"]}')
                    self.stdout.write(f'   Slips Skipped:   {stats["skipped"]}')
                    self.stdout.write(f'   Errors:          {stats["errors"]}')
                    self.stdout.write(f'\n   Gross Total:     {stats["total_gross"]:,.2f} AED')
                    self.stdout.write(f'   Total Deductions:{stats["total_deductions"]:,.2f} AED')
                    self.stdout.write(f'   Net Total:       {stats["total_net"]:,.2f} AED')
                    
                    self.stdout.write('\n' + '=' * 80)
                    self.stdout.write(self.style.SUCCESS(
                        '✅ Payroll generation completed successfully!'
                    ))
                    self.stdout.write('\n💡 Next Steps:')
                    self.stdout.write('   1. Review slips at: http://localhost:5173/hr/payroll')
                    self.stdout.write('   2. Submit for HR approval when ready')
                    self.stdout.write('   3. Master Payroll File will be generated automatically')
                
                elif result['status'] == 'skipped':
                    self.stdout.write(self.style.WARNING(
                        f'\n⏭️  Generation skipped: {result["reason"]}'
                    ))
                    if 'existing_run_id' in result:
                        self.stdout.write(f'   Existing run: {result["existing_run_id"]}')
                
                elif result['status'] == 'validation_failed':
                    self.stdout.write(self.style.ERROR(
                        f'\n❌ Validation failed: {result["reason"]}'
                    ))
                    self.stdout.write('\n💡 Try with --force to skip validation gates')
                
                elif result['status'] == 'disabled':
                    self.stdout.write(self.style.WARNING(
                        '\n⏸️  Automated payroll generation is disabled'
                    ))
                    self.stdout.write('   Set AUTO_GENERATE_ENABLED=True in payroll_automation.py')
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ Error: {e}'))
            import traceback
            self.stdout.write(traceback.format_exc())
