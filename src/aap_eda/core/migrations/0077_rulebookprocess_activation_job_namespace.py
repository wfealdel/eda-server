from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0076_rulebookprocess_log_retention_checkpoint"),
    ]

    operations = [
        migrations.AddField(
            model_name="rulebookprocess",
            name="activation_job_namespace",
            field=models.TextField(blank=True, null=True),
        ),
    ]
