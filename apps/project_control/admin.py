from django.contrib import admin

from .models import (
    ChangeEvent,
    CostSnapshot,
    Estimate,
    EstimateLineItem,
    ProjectDocument,
    WBSNode,
)


@admin.register(Estimate)
class EstimateAdmin(admin.ModelAdmin):
    list_display = ('project', 'kind', 'version', 'status', 'source', 'total_amount', 'currency', 'snapshot_date')
    list_filter = ('kind', 'status', 'source')
    search_fields = ('project__code', 'project__name', 'title')
    autocomplete_fields = ('project',)


@admin.register(EstimateLineItem)
class EstimateLineItemAdmin(admin.ModelAdmin):
    list_display = ('estimate', 'wbs_code', 'discipline', 'quantity', 'unit_rate', 'line_total')
    search_fields = ('estimate__project__code', 'wbs_code', 'description')
    list_filter = ('discipline',)


@admin.register(WBSNode)
class WBSNodeAdmin(admin.ModelAdmin):
    list_display = ('project', 'code', 'name', 'level', 'parent')
    search_fields = ('project__code', 'code', 'name')


@admin.register(ProjectDocument)
class ProjectDocumentAdmin(admin.ModelAdmin):
    list_display = ('project', 'kind', 'original_filename', 'size_bytes', 'parse_status', 'uploaded_by', 'created_at')
    list_filter = ('kind', 'parse_status')
    search_fields = ('project__code', 'original_filename', 'title')
    readonly_fields = ('size_bytes', 'parse_status', 'parsed_data', 'parse_error')


@admin.register(CostSnapshot)
class CostSnapshotAdmin(admin.ModelAdmin):
    list_display = ('project', 'period_end', 'planned_value', 'earned_value', 'actual_cost', 'cpi', 'spi')
    list_filter = ('source',)
    search_fields = ('project__code',)


@admin.register(ChangeEvent)
class ChangeEventAdmin(admin.ModelAdmin):
    list_display = ('project', 'summary', 'severity', 'status', 'delta_amount', 'detected_at')
    list_filter = ('severity', 'status')
    search_fields = ('project__code', 'summary')
