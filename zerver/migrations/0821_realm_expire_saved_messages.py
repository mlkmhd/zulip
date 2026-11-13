from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0820_realm_enable_reaction_notifications"),
    ]

    operations = [
        migrations.AddField(
            model_name="realm",
            name="expire_saved_messages",
            field=models.BooleanField(default=False, db_default=False),
        ),
    ]
