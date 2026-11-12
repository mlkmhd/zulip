from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0816_enable_full_width_by_default"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="web_ui_version",
            field=models.CharField(blank=True, default="", max_length=7),
        ),
    ]
