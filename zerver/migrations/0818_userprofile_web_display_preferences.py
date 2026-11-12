from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0817_userprofile_web_ui_version"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name=name,
            field=models.CharField(default="auto", max_length=12),
        )
        for name in ("web_calendar", "web_font")
    ]
