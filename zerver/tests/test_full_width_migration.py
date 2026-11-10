from django.db.migrations.state import StateApps
from typing_extensions import override

from zerver.lib.test_classes import MigrationsTestCase


class EnableFullWidthTest(MigrationsTestCase):
    migrate_from = "0815_merge"
    migrate_to = "0816_enable_full_width_by_default"

    @override
    def setUpBeforeMigration(self, apps: StateApps) -> None:
        RealmUserDefault = apps.get_model("zerver", "RealmUserDefault")
        UserProfile = apps.get_model("zerver", "UserProfile")
        RealmUserDefault.objects.update(fluid_layout_width=False)
        UserProfile.objects.update(fluid_layout_width=False)
        UserProfile.objects.filter(delivery_email="hamlet@zulip.com").update(
            fluid_layout_width=True
        )

    def test_enable_full_width_for_existing_users_and_organizations(self) -> None:
        RealmUserDefault = self.apps.get_model("zerver", "RealmUserDefault")
        UserProfile = self.apps.get_model("zerver", "UserProfile")
        self.assertFalse(RealmUserDefault.objects.filter(fluid_layout_width=False).exists())
        self.assertFalse(UserProfile.objects.filter(fluid_layout_width=False).exists())
