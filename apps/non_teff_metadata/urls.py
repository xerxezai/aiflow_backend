from django.urls import path

from . import batch_views, project_views, smart_views, views

urlpatterns = [
    # Projects (additive — RBAC-aligned organisational layer)
    path('projects/',                          project_views.projects_collection,  name='non-teff-projects'),
    path('projects/<uuid:project_id>/',        project_views.project_detail,       name='non-teff-project-detail'),
    path('projects/<uuid:project_id>/items/',  project_views.list_project_items,   name='non-teff-project-items'),

    # Smart Features (additive — analytic post-extraction insights)
    path('smart/confidence/',   smart_views.smart_confidence,   name='non-teff-smart-confidence'),
    path('smart/repair/',       smart_views.smart_repair,       name='non-teff-smart-repair'),
    path('smart/consistency/',  smart_views.smart_consistency,  name='non-teff-smart-consistency'),
    path('smart/query/',        smart_views.smart_query,        name='non-teff-smart-query'),
    path('smart/classify/',     smart_views.smart_classify,     name='non-teff-smart-classify'),
    path('smart/auto-link/',    smart_views.smart_auto_link,    name='non-teff-smart-autolink'),
    path('smart/timeline/',     smart_views.smart_timeline,     name='non-teff-smart-timeline'),
    path('smart/bulk-suggest/', smart_views.smart_bulk_suggest, name='non-teff-smart-bulk'),

    # Existing single-file workflow (unchanged)
    path('upload/', views.upload_non_teff_file, name='non-teff-upload'),
    path('status/<str:job_id>/', views.get_non_teff_status, name='non-teff-status'),
    path('results/<str:job_id>/', views.get_non_teff_results, name='non-teff-results'),
    path('export/<str:job_id>/', views.export_non_teff_excel, name='non-teff-export'),

    # Bulk Master Index workflow (additive)
    path('batch/template/', batch_views.get_batch_template, name='non-teff-batch-template'),
    path('batch/create/',   batch_views.create_batch,       name='non-teff-batch-create'),
    path('batch/<uuid:batch_id>/upload/',                batch_views.upload_batch_files,  name='non-teff-batch-upload'),
    # Direct-to-S3 presigned upload (large-file path, bypasses Railway edge cap)
    path('batch/<uuid:batch_id>/upload/presign/',        batch_views.presign_batch_upload,  name='non-teff-batch-upload-presign'),
    path('batch/<uuid:batch_id>/upload/complete/',       batch_views.complete_batch_upload, name='non-teff-batch-upload-complete'),
    path('batch/<uuid:batch_id>/start/',                 batch_views.start_batch,         name='non-teff-batch-start'),
    path('batch/<uuid:batch_id>/status/',                batch_views.batch_status,        name='non-teff-batch-status'),
    path('batch/<uuid:batch_id>/items/',                 batch_views.list_batch_items,    name='non-teff-batch-items'),
    path('batch/<uuid:batch_id>/items/<uuid:item_id>/',  batch_views.update_batch_item,   name='non-teff-batch-item-update'),
    path('batch/<uuid:batch_id>/bulk-update/',           batch_views.bulk_update_items,   name='non-teff-batch-bulk-update'),
    path('batch/<uuid:batch_id>/export/',                batch_views.export_batch,        name='non-teff-batch-export'),

    # Document Search Canvas (additive — read-only locator + page renderer)
    path('search/',                                                          batch_views.search_documents,    name='non-teff-search'),
    path('batch/<uuid:batch_id>/items/<uuid:item_id>/locate/',               batch_views.locate_in_item,      name='non-teff-search-locate'),
    path('batch/<uuid:batch_id>/items/<uuid:item_id>/page/<int:page_no>/image/', batch_views.get_item_page_image, name='non-teff-search-page-image'),
    path('batch/<uuid:batch_id>/items/<uuid:item_id>/recommend/',            batch_views.recommend_for_item,  name='non-teff-item-recommend'),
    path('batch/<uuid:batch_id>/items/<uuid:item_id>/yellow/',               batch_views.yellow_regions_for_item, name='non-teff-item-yellow'),
    path('batch/<uuid:batch_id>/coverage/',                                  batch_views.batch_coverage,      name='non-teff-batch-coverage'),
    path('batch/<uuid:batch_id>/reconcile/',                                 batch_views.batch_reconcile,     name='non-teff-batch-reconcile'),

    # Direct-link to original drawing/record (additive — saves search time)
    path('batch/<uuid:batch_id>/items/<uuid:item_id>/file/',                 batch_views.download_item_file,  name='non-teff-item-file'),
    path('batch/<uuid:batch_id>/items/<uuid:item_id>/location/',             batch_views.item_location,       name='non-teff-item-location'),

    # SmartPlant Foundation integration (additive — config-driven)
    path('smartplant/status/',                              batch_views.smartplant_status, name='non-teff-smartplant-status'),
    path('batch/<uuid:batch_id>/smartplant/status/',        batch_views.smartplant_status, name='non-teff-smartplant-batch-status'),
    path('batch/<uuid:batch_id>/smartplant/push/',          batch_views.smartplant_push,   name='non-teff-smartplant-push'),

    # History (additive — role-based, S3-backed, doesn't touch core logic)
    path('history/',                  views.list_non_teff_history, name='non-teff-history-list'),
    path('history/diagnostics/',      views.non_teff_archive_diagnostics, name='non-teff-history-diag'),
    path('history/<str:job_id>/',     views.load_non_teff_history, name='non-teff-history-load'),
    path('history/<str:job_id>/delete/', views.delete_non_teff_history, name='non-teff-history-delete'),
    path('history/<str:job_id>/update/', views.update_non_teff_history, name='non-teff-history-update'),
]
