import os
from importlib import import_module

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models import CASCADE
from django.db.models.signals import post_delete, post_save

from zerver.lib.cache import (
    cache_delete,
    get_all_cache_key_prefixes,
    get_cache_backend,
    realm_alert_words_automaton_cache_key,
    realm_alert_words_cache_key,
    remote_cache_stats_finish,
    remote_cache_stats_start,
    validate_cache_key,
)
from zerver.models.realms import Realm
from zerver.models.users import UserProfile

_rust_alert_word_cache = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_ALERT_WORD_CACHE") == "1"
    else None
)


class AlertWord(models.Model):
    # Realm isn't necessary, but it's a nice denormalization.  Users
    # never move to another realm, so it's static, and having Realm
    # here optimizes the main query on this table, which is fetching
    # all the alert words in a realm.
    realm = models.ForeignKey(Realm, db_index=True, on_delete=CASCADE)
    user_profile = models.ForeignKey(UserProfile, on_delete=CASCADE)
    # Case-insensitive name for the alert word.
    word = models.TextField()

    class Meta:
        unique_together = ("user_profile", "word")


def flush_realm_alert_words(realm_id: int) -> None:
    keys = (realm_alert_words_cache_key(realm_id), realm_alert_words_automaton_cache_key(realm_id))
    if _rust_alert_word_cache is None:
        for key in keys:
            cache_delete(key)
        return

    config = settings.CACHES["default"]
    location = config.get("LOCATION")
    options = config.get("OPTIONS", {})
    if (
        config.get("BACKEND") != "zerver.lib.singleton_bmemcached.SingletonBMemcached"
        or not isinstance(location, str)
        or not isinstance(options, dict)
        or options.get("username", config.get("USERNAME", os.environ.get("MEMCACHE_USERNAME")))
        or options.get("password", config.get("PASSWORD", os.environ.get("MEMCACHE_PASSWORD")))
        or options.get("tls_context") is not None
    ):
        raise ImproperlyConfigured("Rust alert-word cache requires unauthenticated local Memcached")

    backend = get_cache_backend(None)
    remote_cache_stats_start()
    try:
        prefixes = get_all_cache_key_prefixes()
        wire_keys = []
        for key in keys:
            for prefix in prefixes:
                prefixed_key = prefix + key
                validate_cache_key(prefixed_key, auto_prepend_prefix=False)
                wire_keys.append(backend.make_and_validate_key(prefixed_key))
        _rust_alert_word_cache.delete_memcached_keys(location, wire_keys)
    finally:
        remote_cache_stats_finish()


def flush_alert_word(*, instance: AlertWord, **kwargs: object) -> None:
    realm_id = instance.realm_id
    flush_realm_alert_words(realm_id)


post_save.connect(flush_alert_word, sender=AlertWord)
post_delete.connect(flush_alert_word, sender=AlertWord)
