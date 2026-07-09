from django.apps import AppConfig


class PayrollEngineConfig(AppConfig):
    """Payroll Engine — fresh, soft-coded monthly payroll automation.

    Owns the monthly cycle: employee master, PayrollRun, Payslip,
    PayslipLineItem, adjustments, and the 4-stage workflow
    (Draft → HR Approved → Finance Approved → Released).

    Independent of legacy apps.finance.salary_models. Sibling app
    apps.payroll keeps leave / attendance / dashboard endpoints alive.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.payroll_engine'
    verbose_name = 'Payroll Engine'
