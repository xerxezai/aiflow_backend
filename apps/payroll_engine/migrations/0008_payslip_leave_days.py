"""Add annual_leave_days and unpaid_leave_days to Payslip.

Populated at run generation from approved LeaveRequest records (apps.payroll)
for the payroll month, matched by employee_code ↔ PayrollEmployee.employee_no.
Categories controlled by catalog.LEAVE_CATEGORIES_FOR_PAYROLL (soft-coded).
"""
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll_engine', '0007_payrollrun_public_holidays'),
    ]

    operations = [
        migrations.AddField(
            model_name='payslip',
            name='annual_leave_days',
            field=models.DecimalField(
                max_digits=8, decimal_places=2, default=Decimal('0.00'),
                help_text='Approved Annual Leave days taken in this payroll month.',
            ),
        ),
        migrations.AddField(
            model_name='payslip',
            name='unpaid_leave_days',
            field=models.DecimalField(
                max_digits=8, decimal_places=2, default=Decimal('0.00'),
                help_text='Approved Unpaid Leave days taken in this payroll month.',
            ),
        ),
    ]
