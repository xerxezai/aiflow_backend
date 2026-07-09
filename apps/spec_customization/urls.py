"""Spec Customization — URL routing."""
from django.urls import path

from . import views, project_views, matching_views

app_name = 'spec_customization'

urlpatterns = [
    # Project organiser (additive — independent of extraction pipeline)
    path('projects/',                          project_views.projects_collection, name='projects'),
    path('projects/<uuid:project_id>/',        project_views.project_detail,      name='project-detail'),
    path('projects/<uuid:project_id>/items/',  project_views.list_project_items,  name='project-items'),

    # Paper Spec extraction
    path('paper-spec/upload/',                 views.upload_paper_spec, name='upload'),
    # Direct-to-S3 presigned upload (large-file path)
    path('paper-spec/upload/presign/',  views.presign_paper_spec_upload,  name='upload-presign'),
    path('paper-spec/upload/complete/', views.complete_paper_spec_upload, name='upload-complete'),
    path('paper-spec/jobs/',                   views.list_jobs,         name='jobs'),
    path('paper-spec/jobs/<uuid:job_id>/',     views.job_detail,        name='job-detail'),
    path('paper-spec/jobs/<uuid:job_id>/classes/', views.job_classes,   name='job-classes'),
    path('paper-spec/jobs/<uuid:job_id>/cancel/',  views.cancel_job,    name='job-cancel'),
    path('paper-spec/jobs/<uuid:job_id>/export/',  views.export_job,    name='job-export'),
    path('paper-spec/jobs/<uuid:job_id>/export-spec/', views.export_smartplant_spec, name='job-export-spec'),
    path('paper-spec/jobs/<uuid:job_id>/export-cat/',  views.export_smartplant_cat,  name='job-export-cat'),
    path('paper-spec/jobs/<uuid:job_id>/workbook/',      views.workbook_preview, name='job-workbook-preview'),
    path('paper-spec/jobs/<uuid:job_id>/workbook/cell/', views.workbook_cell,    name='job-workbook-cell'),
    path('paper-spec/jobs/<uuid:job_id>/workbook/batch-save/', views.workbook_batch_save, name='job-workbook-batch-save'),
    path('paper-spec/jobs/<uuid:job_id>/workbook/delete-row/', views.workbook_delete_row, name='job-workbook-delete-row'),
    path('paper-spec/jobs/<uuid:job_id>/workbook/bulk-delete/', views.workbook_bulk_delete_rows, name='job-workbook-bulk-delete'),
    path('paper-spec/classes/<uuid:class_id>/',    views.class_detail,  name='class-detail'),

    # Component Matching (Match/SPEC/CAT workbooks)
    path('matching/upload/',                                      matching_views.upload_matching_workbooks,      name='matching-upload'),
    path('matching/sets/',                                        matching_views.list_matching_workbook_sets,    name='matching-sets'),
    path('matching/sets/<uuid:set_id>/',                          matching_views.matching_workbook_set_detail,   name='matching-set-detail'),
    path('matching/sets/<uuid:set_id>/activate/',                 matching_views.activate_matching_workbook_set, name='matching-set-activate'),
    path('matching/sets/<uuid:set_id>/parse/',                    matching_views.parse_matching_workbook,        name='matching-set-parse'),
    path('matching/sets/<uuid:set_id>/rules/',                    matching_views.list_matching_rules,            name='matching-rules'),
    path('matching/match/',                                       matching_views.match_component_endpoint,       name='matching-match'),
    path('matching/results/',                                     matching_views.list_matching_results,          name='matching-results'),

    # Config
    path('config/', views.config_view, name='config'),
]
