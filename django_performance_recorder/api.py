# -*- coding:utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from threading import local

from django.core.cache import DEFAULT_CACHE_ALIAS

from .cache import AllCacheRecorder
from .utils import current_test
from .yaml import KVFile


record_current = local()


def record(file_name=None, record_name=None):
    test_details = current_test()

    if file_name is None:
        file_name = test_details.file_path
        if file_name.endswith('.py'):
            file_name = file_name[:-len('.py')] + '.perf.yml'
        else:
            file_name += '.perf.yml'

    if record_name is None:
        if test_details.class_name:
            record_name = '{class_}.{test}'.format(
                class_=test_details.class_name,
                test=test_details.test_name,
            )
        else:
            record_name = test_details.test_name

        # Multiple calls inside the same test should end up suffixing with .2, .3 etc.
        if getattr(record_current, 'record_name', None) == record_name:
            record_current.counter += 1
            record_name = record_name + '.{}'.format(record_current.counter)
        else:
            record_current.record_name = record_name
            record_current.counter = 1

    return PerformanceRecorder(file_name, record_name)


class PerformanceRecorder(object):

    def __init__(self, file_name, record_name):
        self.file_name = file_name
        self.record_name = record_name

        self.record = []
        self.cache_recorder = AllCacheRecorder(self.on_cache_op)

    def __enter__(self):
        self.cache_recorder.__enter__()
        self.load_recordings()

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.cache_recorder.__exit__(exc_type, exc_value, exc_traceback)

        if exc_type is None:
            self.save_or_assert()

    def on_cache_op(self, cache_op):
        name_parts = ['cache']
        if cache_op.cache_name != DEFAULT_CACHE_ALIAS:
            name_parts.append(cache_op.cache_name)
        name_parts.append(cache_op.operation)
        name = '|'.join(name_parts)

        self.record.append({name: cache_op.key_or_keys})

    def load_recordings(self):
        self.records_file = KVFile(self.file_name)

    def save_or_assert(self):
        orig_record = self.records_file.get(self.record_name, None)

        if orig_record is not None:
            assert self.record == orig_record, "Performance record did not match for {}".format(self.record_name)

        self.records_file.set_and_save(self.record_name, self.record)
