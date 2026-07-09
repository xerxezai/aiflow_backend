"""Django admin registration for the Payroll Engine."""
from django.contrib import admin

from .models import (
    PayrollAdjustment, PayrollEmployee, PayrollRun, Payslip, PayslipLineItem,
    PayrollWorkflowLog,
)


@admin.register(PayrollEmployee)
class PayrollEmployeeAdmin(admin.ModelAdmin):
    list_display = ('employee_no', 'full_name', 'department', 'designation',
                    'basic', 'housing', 'transport', 'home_leave', 'is_active')
    list_filter = ('is_active', 'department', 'discipline', 'nationality_group')
    search_fields = ('employee_no', 'full_name', 'iban', 'mol_no')
    ordering = ('full_name',)


class PayslipInline(admin.TabularInline):
    model = Payslip
    extra = 0
    readonly_fields = ('employee', 'gross_earnings', 'total_deductions', 'net_payable', 'status')
    fields = readonly_fields
    show_change_link = True


@admin.register(PayrollRun)
class PayrollRunAdmin(admin.ModelAdmin):
    list_display = ('cycle_code', 'status', 'employee_count',
                    'total_gross', 'total_deductions', 'total_net', 'generated_at')
    list_filter = ('status', 'year')
    search_fields = ('cycle_code', 'notes')
    inlines = [PayslipInline]


class PayslipLineItemInline(admin.TabularInline):
    model = PayslipLineItem
    extra = 0


@admin.register(Payslip)
class PayslipAdmin(admin.ModelAdmin):
    list_display = ('snapshot_full_name', 'run', 'gross_earnings',
                    'total_deductions', 'net_payable', 'status', 'payment_mode')
    list_filter = ('run', 'status', 'payment_mode')
    search_fields = ('snapshot_full_name', 'employee__employee_no')
    inlines = [PayslipLineItemInline]


@admin.register(PayslipLineItem)
class PayslipLineItemAdmin(admin.ModelAdmin):
    list_display = ('payslip', 'kind', 'component_code', 'label', 'amount', 'source')
    list_filter = ('kind', 'source', 'component_code')
    search_fields = ('label', 'description')


@admin.register(PayrollAdjustment)
class PayrollAdjustmentAdmin(admin.ModelAdmin):
    list_display = ('employee', 'target_year', 'target_month', 'kind',
                    'component_code', 'amount', 'status')
    list_filter = ('status', 'target_year', 'target_month', 'kind')
    search_fields = ('employee__employee_no', 'employee__full_name', 'label')


@admin.register(PayrollWorkflowLog)
class PayrollWorkflowLogAdmin(admin.ModelAdmin):
    list_display = ('run', 'from_status', 'to_status', 'actor', 'at')
    list_filter = ('to_status',)
    readonly_fields = ('run', 'from_status', 'to_status', 'actor', 'note', 'at')
