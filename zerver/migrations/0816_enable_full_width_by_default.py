from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def enable_full_width(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    RealmUserDefault = apps.get_model("zerver", "RealmUserDefault")
    UserProfile = apps.get_model("zerver", "UserProfile")
    RealmUserDefault.objects.filter(fluid_layout_width=False).update(fluid_layout_width=True)
    UserProfile.objects.filter(fluid_layout_width=False).update(fluid_layout_width=True)


class Migration(migrations.Migration):
    dependencies = [
        ("zerver", "0815_merge"),
    ]

    operations = [
        migrations.AlterField(
            model_name="realmuserdefault",
            name="fluid_layout_width",
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name="userprofile",
            name="fluid_layout_width",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(
            enable_full_width, reverse_code=migrations.RunPython.noop, elidable=True
        ),
    ]
