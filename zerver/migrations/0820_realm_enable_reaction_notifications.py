from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0819_fork_stream_icon_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="realm",
            name="enable_reaction_notifications",
            field=models.BooleanField(default=True, db_default=True),
        ),
    ]
