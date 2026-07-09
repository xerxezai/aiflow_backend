import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from apps.spec_customization.models import PaperSpecExtractionJob

job = PaperSpecExtractionJob.objects.order_by('-created_at').first()
print('job:', job.id, job.created_at)
classes = list(job.piping_classes.all())
print('classes:', len(classes))
for cls in classes[:3]:
    print(f'  {cls.class_code} components={cls.components.count()}')
    by_type = {}
    for c in cls.components.all():
        by_type.setdefault(c.component_type, 0)
        by_type[c.component_type] += 1
    print(f'    types: {by_type}')
