import inspect
import re
from collections.abc import Mapping, Sequence
from functools import wraps
from types import MethodType

from django.conf import settings
from django.core.cache import caches

from .utils import sorted_names


class CacheOp(object):
    def __init__(self, alias, operation, key_or_keys):
        self.alias = alias
        self.operation = operation
        if isinstance(key_or_keys, str):
            self.key_or_keys = self.clean_key(key_or_keys)
        elif isinstance(key_or_keys, (Mapping, Sequence)):
            self.key_or_keys = sorted(self.clean_key(k) for k in key_or_keys)
        else:
            raise ValueError("key_or_keys must be a string, mapping, or sequence")

    @classmethod
    def clean_key(cls, key):
        """
        Replace things that look like variables with a '#' so tests aren't
        affected by random variables
        """
        for var_re in cls.VARIABLE_RES:
            key = var_re.sub("#", key)
        return key

    VARIABLE_RES = (
        # Django session keys for 'cache' backend
        re.compile(r"(?<=django\.contrib\.sessions\.cache)[0-9a-z]{32}\b"),
        # Django session keys for 'cached_db' backend
        re.compile(r"(?<=django\.contrib\.sessions\.cached_db)[0-9a-z]{32}\b"),
        # Long random hashes
        re.compile(r"\b[0-9a-f]{32}\b"),
        # UUIDs
        re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),
        # Integers
        re.compile(r"\d+"),
    )

    def __eq__(self, other):
        return (
            isinstance(other, CacheOp)
            and self.alias == other.alias
            and self.operation == other.operation
            and self.key_or_keys == other.key_or_keys
        )


class CacheRecorder(object):
    """
    Monkey patches a cache class to call 'callback' on every operation it calls
    """

    def __init__(self, alias, callback):
        self.alias = alias
        self.callback = callback

    def __enter__(self):
        cache = caches[self.alias]

        def call_callback(func):
            alias = self.alias
            callback = self.callback

            @wraps(func)
            def inner(self, *args, **kwargs):
                # Ignore operations from the cache class calling itself

                # Get the self of the parent via stack inspection
                frame = inspect.currentframe()
                try:
                    frame = frame.f_back
                    is_internal_call = frame.f_locals.get("self", None) is self
                finally:
                    # Always delete frame references to help garbage collector
                    del frame

                if not is_internal_call:
                    callback(
                        CacheOp(
                            alias=alias,
                            operation=str(func.__name__),
                            key_or_keys=args[0],
                        )
                    )

                return func(*args, **kwargs)

            return inner

        self.orig_methods = {name: getattr(cache, name) for name in self.cache_methods}
        for name in self.cache_methods:
            orig_method = self.orig_methods[name]
            setattr(cache, name, MethodType(call_callback(orig_method), cache))

    def __exit__(self, _, __, ___):
        cache = caches[self.alias]
        for name in self.cache_methods:
            setattr(cache, name, self.orig_methods[name])
        del self.orig_methods

    cache_methods = (
        "add",
        "decr",
        "delete",
        "delete_many",
        "get",
        "get_many",
        "incr",
        "set",
        "set_many",
    )


class AllCacheRecorder(object):
    """
    Launches CacheRecorders on all the active caches
    """

    def __init__(self, callback):
        self.callback = callback

    def __enter__(self):
        self.recorders = []
        for name in sorted_names(settings.CACHES.keys()):
            recorder = CacheRecorder(name, self.callback)
            recorder.__enter__()
            self.recorders.append(recorder)

    def __exit__(self, type_, value, traceback):
        for recorder in reversed(self.recorders):
            recorder.__exit__(type_, value, traceback)
        self.recorders = []
