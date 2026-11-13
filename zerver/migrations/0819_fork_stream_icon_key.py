from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0818_userprofile_web_display_preferences"),
    ]

    operations = [
        migrations.AddField(
            model_name="stream",
            name="icon_key",
            field=models.UUIDField(null=True),
        ),
    ]
