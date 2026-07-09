from django.contrib import admin
from apps.core.project_models import Project, ProjectMember, ProjectTask, ProjectMilestone
from apps.core.models import Enquiry


@admin.register(Enquiry)
class EnquiryAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'email', 'company', 'subject', 'service', 'urgency', 'status', 'created_at']
    list_filter = ['status', 'urgency', 'service', 'created_at']
    search_fields = ['name', 'email', 'company', 'subject', 'message']
    readonly_fields = ['created_at', 'updated_at', 'source_ip', 'user_agent']
    list_per_page = 50


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'status', 'priority', 'progress', 'owner', 'created_at']
    list_filter = ['status', 'priority', 'created_at']
    search_fields = ['name', 'code', 'description', 'client_name']
    readonly_fields = ['created_at', 'updated_at', 'is_overdue', 'budget_utilization', 'team_size']


@admin.register(ProjectMember)
class ProjectMemberAdmin(admin.ModelAdmin):
    list_display = ['project', 'user', 'role', 'joined_at', 'is_active']
    list_filter = ['role', 'is_active', 'joined_at']
    search_fields = ['project__name', 'user__email']


@admin.register(ProjectTask)
class ProjectTaskAdmin(admin.ModelAdmin):
    list_display = ['title', 'project', 'status', 'priority', 'assigned_to', 'due_date']
    list_filter = ['status', 'priority', 'created_at']
    search_fields = ['title', 'description', 'project__name']


@admin.register(ProjectMilestone)
class ProjectMilestoneAdmin(admin.ModelAdmin):
    list_display = ['name', 'project', 'target_date', 'is_completed', 'completed_date']
    list_filter = ['is_completed', 'target_date']
    search_fields = ['name', 'description', 'project__name']

