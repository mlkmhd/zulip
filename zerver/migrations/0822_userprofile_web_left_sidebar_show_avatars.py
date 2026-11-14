from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0821_realm_expire_saved_messages"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="web_left_sidebar_show_avatars",
            field=models.BooleanField(default=True, db_default=True),
        ),
    ]
