"""
Django management command to import payroll data from Excel file - FLEXIBLE VERSION
Works for any month/year with automatic configuration

Usage Examples:
  # Import June 2026 payroll
  docker exec aiflow_backend_local python manage.py import_payroll --month 6 --year 2026 --excel "/app/media/payroll/June_2026_Payroll.xlsx"
  
  # Import with custom sheet name
  docker exec aiflow_backend_local python manage.py import_payroll --month 6 --year 2026 --excel "/app/media/payroll/June_2026.xlsx" --sheet "JUNE 2026"
  
  # Production database import
  docker exec aiflow_backend_local bash -c 'export DATABASE_URL="postgresql://postgres:..." && python manage.py import_payroll --month 6 --year 2026 --excel "/app/media/payroll/June_2026.xlsx"'
"""
import os
import pandas as pd
from decimal import Decimal
from datetime import date, datetime
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from apps.finance.salary_models import (
    PayrollRun, SalarySlip, EmployeeSalaryInfo
)

User = get_user_model()

# ═══════════════════════════════════════════════════════════════════════════
# SOFT-CODED CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

# Default salary structure
DEFAULT_CURRENCY = 'AED'
DEFAULT_PAYMENT_FREQUENCY = 'monthly'

# Column name mapping (flexible matching - supports multiple Excel formats)
COLUMN_MAPPING = {
    'employee_no': ['Employee Number', 'employee number', 'Employee No.', 'Emp Code', 'EMP CODE'],
    'name': ['Employee', 'Name', 'Employee Name', 'Full Name'],
    'title': ['Position', 'Title', 'Designation', 'Job Title'],
    'department': ['Dep.', 'Department', 'Dept'],
    'discipline': ['Discipline', 'Disc'],
    'joining_date': ['Joining date', 'Joining Date', 'Join Date', 'Date of Joining'],
    'basic_salary': ['Basic', 'Basic Salary', 'Base Salary'],
    'housing_allowance': ['Housing', 'Housing Allowance', 'HRA'],
    'transport_allowance': ['Transpo', 'Transport', 'Transportation Allowance', 'Transportation'],
    'home_leave_allowance': ['Home Leave', 'Leave allowance', 'Home Leave Allowance'],
    'other_allowance': ['Other Allowance', 'Other Allow.'],
    'others': ['Other\nPay', 'Others', 'Other Pay', 'Additional Pay'],
    'other_pay_details': ['Other Pay Details', 'Pay Details', 'Details'],
    'gross_salary': ['Gross Salary', 'Gross', 'Total Gross'],
    'salary_deduction': ['Salary Deduction', 'Deduction', 'Deductions', 'Total Deductions'],
    'deduction_details': ['Salary Deduction Details', 'Deduction Details', 'Deduction Notes'],
    'net_salary': ['FINAL Remuneration', 'Net Salary', 'Net', 'Final Salary', 'Take Home'],
}


class Command(BaseCommand):
    help = 'Import payroll data from Excel file for any month/year'

    def add_arguments(self, parser):
        parser.add_argument(
            '--month',
            type=int,
            required=True,
            help='Payroll month (1-12)'
        )
        parser.add_argument(
            '--year',
            type=int,
            required=True,
            help='Payroll year (e.g., 2026)'
        )
        parser.add_argument(
            '--excel',
            type=str,
            required=True,
            help='Path to Excel payroll file'
        )
        parser.add_argument(
            '--sheet',
            type=str,
            default=None,
            help='Sheet name to import (auto-detect if not specified)'
        )
        parser.add_argument(
            '--header-row',
            type=int,
            default=None,
            help='Header row index (0-based). Auto-detect if not specified'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Perform a dry run without saving to database'
        )
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Overwrite existing payroll run for this month/year'
        )

    def handle(self, *args, **options):
        month = options['month']
        year = options['year']
        excel_file = options['excel']
        sheet_name = options['sheet']
        header_row = options['header_row']
        dry_run = options['dry_run']
        overwrite = options['overwrite']

        # Validate month
        if not 1 <= month <= 12:
            self.stdout.write(self.style.ERROR(f'Invalid month: {month}. Must be 1-12'))
            return

        # Check if file exists
        if not os.path.exists(excel_file):
            self.stdout.write(self.style.ERROR(f'Excel file not found: {excel_file}'))
            return

        # Generate payroll run code
        month_names = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
        run_code = f'PAY-{month_names[month-1]}-{year}'

        # Check if payroll run already exists
        if not overwrite:
            existing_run = PayrollRun.objects.filter(month=month, year=year).first()
            if existing_run:
                self.stdout.write(self.style.WARNING(
                    f'\n⚠️  Payroll run already exists: {existing_run.run_code}'
                ))
                self.stdout.write('Use --overwrite to replace it, or delete it first with:')
                self.stdout.write(f'  python manage.py clear_payroll_run --run-code {existing_run.run_code}')
                return

        self.stdout.write('='*80)
        self.stdout.write(f'PAYROLL IMPORT - {month_names[month-1]} {year}')
        self.stdout.write('='*80)
        self.stdout.write(f'\nExcel file: {excel_file}')
        
        # Auto-detect sheet name if not provided
        if not sheet_name:
            sheet_name = self.detect_sheet_name(excel_file, month, year)
            
        self.stdout.write(f'Sheet: {sheet_name}')

        try:
            # Auto-detect header row if not provided
            if header_row is None:
                header_row = self.detect_header_row(excel_file, sheet_name)
                
            self.stdout.write(f'Header row: {header_row}')
            
            # Read Excel file
            self.stdout.write('\nReading Excel file...')
            df = pd.read_excel(excel_file, sheet_name=sheet_name, header=header_row, engine='openpyxl')
            self.stdout.write(self.style.SUCCESS(f'✓ Loaded {len(df)} rows from sheet "{sheet_name}"'))

            # Map columns
            column_map = self.map_columns(df.columns)
            
            # Prepare employee data
            self.stdout.write(f'\n✓ Prepared employee data')
            employees_data = self.prepare_employee_data(df, column_map)
            self.stdout.write(self.style.SUCCESS(f'✓ Prepared {len(employees_data)} valid employee records'))

            if dry_run:
                self.stdout.write(self.style.WARNING('\n⚠️  DRY RUN MODE - No data will be saved'))
                self.stdout.write(f'\nWould import {len(employees_data)} employees for {month_names[month-1]} {year}')
                return

            # Process payroll
            with transaction.atomic():
                # Delete existing run if overwrite is True
                if overwrite:
                    existing_run = PayrollRun.objects.filter(month=month, year=year).first()
                    if existing_run:
                        slip_count = SalarySlip.objects.filter(payroll_run=existing_run).count()
                        SalarySlip.objects.filter(payroll_run=existing_run).delete()
                        existing_run.delete()
                        self.stdout.write(self.style.WARNING(f'\n✓ Deleted existing run: {existing_run.run_code} ({slip_count} slips)'))
                
                stats = self.process_payroll(employees_data, month, year, run_code, dry_run)
                
                self.stdout.write('\n' + '='*80)
                self.stdout.write('IMPORT STATISTICS:')
                self.stdout.write('='*80)
                self.stdout.write(f"Employees created: {stats['employees_created']}")
                self.stdout.write(f"Employees updated: {stats['employees_updated']}")
                self.stdout.write(f"Payroll runs created: {stats['runs_created']}")
                self.stdout.write(f"Salary slips created: {stats['slips_created']}")
                self.stdout.write(f"Total gross salary: AED {stats['total_gross']:,.2f}")
                self.stdout.write(f"Total deductions: AED {stats['total_deductions']:,.2f}")
                self.stdout.write(f"Total net salary: AED {stats['total_net']:,.2f}")
                self.stdout.write('='*80)

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ Import failed: {str(e)}'))
            import traceback
            traceback.print_exc()
            raise

    def detect_sheet_name(self, excel_file, month, year):
        """Auto-detect sheet name based on month/year"""
        month_names = ['JANUARY', 'FEBRUARY', 'MARCH', 'APRIL', 'MAY', 'JUNE', 
                       'JULY', 'AUGUST', 'SEPTEMBER', 'OCTOBER', 'NOVEMBER', 'DECEMBER']
        month_short = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
        
        # Get all sheet names
        xls = pd.ExcelFile(excel_file)
        sheets = xls.sheet_names
        
        # Try to find matching sheet
        month_full = month_names[month - 1]
        month_abbr = month_short[month - 1]
        year_short = str(year)[-2:]
        
        for sheet in sheets:
            sheet_upper = sheet.upper()
            # Try various formats: "JUNE 2026", "JUN 2026", "JUNE - 26", etc.
            if (month_full in sheet_upper or month_abbr in sheet_upper) and (str(year) in sheet or year_short in sheet):
                self.stdout.write(f'Auto-detected sheet: {sheet}')
                return sheet
        
        # If no match found, use first sheet
        self.stdout.write(self.style.WARNING(f'Could not auto-detect sheet, using first sheet: {sheets[0]}'))
        return sheets[0]

    def detect_header_row(self, excel_file, sheet_name):
        """Auto-detect header row by looking for 'Employee' or 'Employee Number' column"""
        df = pd.read_excel(excel_file, sheet_name=sheet_name, header=None, nrows=10, engine='openpyxl')
        
        for idx, row in df.iterrows():
            row_str = ' '.join([str(x).lower() for x in row if pd.notna(x)])
            if 'employee' in row_str or 'emp code' in row_str or 'basic' in row_str:
                self.stdout.write(f'Auto-detected header row: {idx}')
                return idx
        
        # Default to row 0
        return 0

    def map_columns(self, excel_columns):
        """Map Excel column names to internal field names (smart: exact then prefix match)"""
        column_map = {}
        
        self.stdout.write('\nMapping columns:')
        self.stdout.write('-'*80)
        
        for internal_name, possible_names in COLUMN_MAPPING.items():
            # Pass 1: exact match
            matched_col = None
            for col in excel_columns:
                if col in possible_names:
                    matched_col = col
                    break
            # Pass 2: prefix match (handles 'FINAL Remuneration April 2026' style)
            if not matched_col:
                for col in excel_columns:
                    col_norm = str(col).strip()
                    for candidate in possible_names:
                        if col_norm.lower().startswith(candidate.lower()):
                            matched_col = col
                            break
                    if matched_col:
                        break
            if matched_col:
                column_map[internal_name] = matched_col
                self.stdout.write(f'✓ {internal_name:25} -> "{matched_col}"')
            else:
                self.stdout.write(f'✗ {internal_name:25} -> NOT FOUND')
        
        return column_map

    def safe_decimal(self, value, default=Decimal('0.00')):
        """Safely convert value to Decimal"""
        if pd.isna(value):
            return default
        try:
            return Decimal(str(value))
        except:
            return default

    def prepare_employee_data(self, df, column_map):
        """Convert DataFrame to list of employee dicts"""
        employees = []
        
        for idx, row in df.iterrows():
            # Get employee number (required)
            emp_no_col = column_map.get('employee_no')
            if not emp_no_col or pd.isna(row.get(emp_no_col)):
                continue
            
            employee_no = str(row[emp_no_col]).strip()
            if not employee_no or employee_no == 'nan':
                continue
            
            # Get name
            name_col = column_map.get('name')
            name = str(row.get(name_col, '')).strip() if name_col else ''
            if not name or name == 'nan':
                continue
            
            # Build employee data dict
            emp_data = {
                'employee_no': employee_no,
                'name': name,
                'title': str(row.get(column_map['title'], '')).strip() if column_map.get('title') else '',
                'department': str(row.get(column_map['department'], '')).strip() if column_map.get('department') else '',
                'discipline': str(row.get(column_map['discipline'], '')).strip() if column_map.get('discipline') else '',
                'joining_date': row.get(column_map['joining_date']) if column_map.get('joining_date') else None,
                'basic_salary': self.safe_decimal(row.get(column_map['basic_salary']) if column_map.get('basic_salary') else None),
                'housing_allowance': self.safe_decimal(row.get(column_map['housing_allowance']) if column_map.get('housing_allowance') else None),
                'transport_allowance': self.safe_decimal(row.get(column_map['transport_allowance']) if column_map.get('transport_allowance') else None),
                'home_leave_allowance': self.safe_decimal(row.get(column_map['home_leave_allowance']) if column_map.get('home_leave_allowance') else None),
                'other_allowance': self.safe_decimal(row.get(column_map['other_allowance']) if column_map.get('other_allowance') else None),
                'others': self.safe_decimal(row.get(column_map['others']) if column_map.get('others') else None),
                'other_pay_details': str(row.get(column_map['other_pay_details'], '')).strip() if column_map.get('other_pay_details') else '',
                'salary_deduction': self.safe_decimal(row.get(column_map['salary_deduction']) if column_map.get('salary_deduction') else None),
                'deduction_details': str(row.get(column_map['deduction_details'], '')).strip() if column_map.get('deduction_details') else '',
                'net_salary': self.safe_decimal(row.get(column_map['net_salary']) if column_map.get('net_salary') else None),
                'gross_salary': Decimal('0.00'),
                'total_deductions': Decimal('0.00'),
            }
            
            # Calculate gross salary
            emp_data['gross_salary'] = (
                emp_data['basic_salary'] +
                emp_data['housing_allowance'] +
                emp_data['transport_allowance'] +
                emp_data['home_leave_allowance'] +
                emp_data['other_allowance'] +
                emp_data['others']
            )
            
            # Use salary_deduction as total_deductions
            emp_data['total_deductions'] = emp_data['salary_deduction']
            
            # Verify net salary calculation
            if emp_data['net_salary'] == Decimal('0.00'):
                emp_data['net_salary'] = emp_data['gross_salary'] - emp_data['total_deductions']
            
            employees.append(emp_data)
        
        return employees

    def process_payroll(self, employees_data, month, year, run_code, dry_run=False):
        """Process payroll data and create/update records"""
        stats = {
            'employees_created': 0,
            'employees_updated': 0,
            'runs_created': 0,
            'slips_created': 0,
            'total_gross': Decimal('0.00'),
            'total_deductions': Decimal('0.00'),
            'total_net': Decimal('0.00'),
        }

        # Calculate period dates
        from calendar import monthrange
        last_day = monthrange(year, month)[1]
        period_start = date(year, month, 1)
        period_end = date(year, month, last_day)

        # Get or create payroll run
        payroll_run, created = PayrollRun.objects.get_or_create(
            month=month,
            year=year,
            defaults={
                'run_code': run_code,
                'status': 'draft',
                'period_start': period_start,
                'period_end': period_end,
            }
        )

        if created:
            stats['runs_created'] += 1
            self.stdout.write(f'\n✓ Created payroll run: {payroll_run.run_code}')
        else:
            self.stdout.write(f'\n✓ Using existing payroll run: {payroll_run.run_code}')

        self.stdout.write('\nProcessing employees:')
        self.stdout.write('-'*80)

        for idx, emp_data in enumerate(employees_data, 1):
            try:
                emp_info, emp_created, slip = self.process_employee(emp_data, payroll_run, month, year)
                
                if emp_created:
                    stats['employees_created'] += 1
                else:
                    stats['employees_updated'] += 1

                if slip:
                    stats['slips_created'] += 1
                    stats['total_gross'] += slip.gross_salary
                    stats['total_deductions'] += slip.total_deductions
                    stats['total_net'] += slip.net_salary

                status = '🆕' if emp_created else '🔄'
                self.stdout.write(
                    f'{idx:3}. {status} {emp_info.user.get_full_name():30} ({emp_info.employee_id:10}) - '
                    f'Net: AED {slip.net_salary if slip else 0:>12,.2f}'
                )

            except Exception as e:
                self.stdout.write(self.style.ERROR(
                    f'{idx:3}. ❌ Failed: {emp_data.get("name", "Unknown")} - {str(e)}'
                ))
                continue

        # Update payroll run totals
        payroll_run.total_employees = stats['slips_created']
        payroll_run.processed_employees = stats['slips_created']
        payroll_run.total_gross_salary = stats['total_gross']
        payroll_run.total_deductions = stats['total_deductions']
        payroll_run.total_net_salary = stats['total_net']
        payroll_run.status = 'completed'
        payroll_run.save()

        return stats

    def process_employee(self, emp_data, payroll_run, month, year):
        """Create/update employee and salary slip"""
        employee_code = emp_data['employee_no']
        full_name = emp_data['name']
        title = emp_data['title']
        department = emp_data.get('department', '')

        # Parse joining date
        joining_date = None
        if emp_data.get('joining_date'):
            try:
                if isinstance(emp_data['joining_date'], pd.Timestamp):
                    joining_date = emp_data['joining_date'].date()
                elif isinstance(emp_data['joining_date'], datetime):
                    joining_date = emp_data['joining_date'].date()
                elif isinstance(emp_data['joining_date'], date):
                    joining_date = emp_data['joining_date']
                elif isinstance(emp_data['joining_date'], str):
                    joining_date = pd.to_datetime(emp_data['joining_date']).date()
            except:
                joining_date = None

        # Split name into first/last
        name_parts = full_name.split() if full_name else ['Employee']
        first_name = name_parts[0] if name_parts else 'Employee'
        last_name = ' '.join(name_parts[1:]) if len(name_parts) > 1 else employee_code

        # Create email from employee code
        email = f'{employee_code}@rejlers.ae'.lower()

        # Get or create user
        user, user_created = User.objects.get_or_create(
            email=email,
            defaults={
                'username': employee_code,
                'first_name': first_name,
                'last_name': last_name,
                'is_active': True,
            }
        )

        # Update user name if it changed
        if not user_created:
            user.first_name = first_name
            user.last_name = last_name
            user.save()

        # Get or create employee salary info
        emp_salary_info, emp_created = EmployeeSalaryInfo.objects.get_or_create(
            employee_id=employee_code,
            defaults={
                'user': user,
                'designation': title,
                'department': department,
                'join_date': joining_date or date.today(),
                'basic_salary': emp_data['basic_salary'],
                'currency': DEFAULT_CURRENCY,
                'is_active': True,
            }
        )

        # Update employee salary info if exists
        if not emp_created:
            emp_salary_info.designation = title
            emp_salary_info.department = department
            emp_salary_info.basic_salary = emp_data['basic_salary']
            if joining_date:
                emp_salary_info.join_date = joining_date
            emp_salary_info.save()

        # Calculate total allowances
        total_allowances = (
            emp_data['housing_allowance'] +
            emp_data['transport_allowance'] +
            emp_data['home_leave_allowance'] +
            emp_data['other_allowance'] +
            emp_data['others']
        )

        # Build allowances breakdown
        allowances_breakdown = {}
        if emp_data['housing_allowance'] > 0:
            allowances_breakdown['Housing Allowance'] = str(emp_data['housing_allowance'])
        if emp_data['transport_allowance'] > 0:
            allowances_breakdown['Transportation Allowance'] = str(emp_data['transport_allowance'])
        if emp_data['home_leave_allowance'] > 0:
            allowances_breakdown['Home Leave Allowance'] = str(emp_data['home_leave_allowance'])
        if emp_data['other_allowance'] > 0:
            allowances_breakdown['Other Allowance'] = str(emp_data['other_allowance'])
        if emp_data['others'] > 0:
            allowances_breakdown['Others'] = str(emp_data['others'])
            if emp_data['other_pay_details']:
                allowances_breakdown['Other Pay Details'] = emp_data['other_pay_details']

        # Build deductions breakdown
        deductions_breakdown = {}
        if emp_data['total_deductions'] > 0:
            deductions_breakdown['Total Deductions'] = str(emp_data['total_deductions'])
            if emp_data['deduction_details']:
                deductions_breakdown['Deduction Details'] = emp_data['deduction_details']

        # Create or update salary slip
        slip_number = f'SLIP-{year}{month:02d}-{employee_code}'
        
        slip, _ = SalarySlip.objects.update_or_create(
            employee_salary_info=emp_salary_info,
            month=month,
            year=year,
            defaults={
                'slip_number': slip_number,
                'payroll_run': payroll_run,
                'basic_salary': emp_data['basic_salary'],
                'total_allowances': total_allowances,
                'gross_salary': emp_data['gross_salary'],
                'total_deductions': emp_data['total_deductions'],
                'net_salary': emp_data['net_salary'],
                'allowances_breakdown': allowances_breakdown,
                'deductions_breakdown': deductions_breakdown,
                'currency': DEFAULT_CURRENCY,
                'status': 'draft',
            }
        )

        return emp_salary_info, emp_created, slip
