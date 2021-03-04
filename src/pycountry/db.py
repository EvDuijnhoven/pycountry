# vim:fileencoding=utf-8
import unicodedata
from io import open
import json
import logging
import threading

logger = logging.getLogger('pycountry.db')


class Data:
    local_keys = ['_fields', '_match_fields', '_match_names', '_match_values']
    name_keys = ['name']
    code_keys = ['code']

    def __init__(self, **fields):
        self._fields = fields
        self._match_fields = {k: self.remove_accents(v.lower()) for (k, v) in self._fields.items()
                              if v is not None and (k in self.name_keys or k in self.code_keys)}
        self._match_values = [f for v in self._match_fields.values() for f in v.split(';')]
        self._match_names = [v for (k, v) in self._match_fields.items() if k in self.name_keys]

    def __getattr__(self, key):
        if key not in self._fields:
            raise AttributeError
        return self._fields[key]

    def __setattr__(self, key, value):
        if key not in self.local_keys:
            self._fields[key] = value
        super(Data, self).__setattr__(key, value)

    def __repr__(self):
        cls_name = self.__class__.__name__
        fields = ', '.join('%s=%r' % i for i in sorted(self._fields.items()))
        return '%s(%s)' % (cls_name, fields)

    def __dir__(self):
        return dir(self.__class__) + list(self._fields)

    @staticmethod
    def remove_accents(input_str):
        # Borrowed from https://stackoverflow.com/a/517974/1509718
        nfkd_form = unicodedata.normalize('NFKD', input_str)
        return u"".join([c for c in nfkd_form if not unicodedata.combining(c)])


def lazy_load(f):
    def load_if_needed(self, *args, **kw):
        if not self._is_loaded:
            with self._load_lock:
                self._load()
        return f(self, *args, **kw)

    return load_if_needed


class Database:
    data_class_base = Data
    data_class_name = None
    root_key = None
    no_index = []

    def __init__(self, filename):
        self.filename = filename
        self._is_loaded = False
        self._load_lock = threading.Lock()

    def _load(self):
        if self._is_loaded:
            # Help keeping the _load_if_needed code easier
            # to read.
            return
        self.objects = []
        self.index_names = set()
        self.indices = {}

        self.data_class = type(
            self.data_class_name, (self.data_class_base,), {})

        with open(self.filename, 'r', encoding="utf-8") as f:
            tree = json.load(f)

        for entry in tree[self.root_key]:
            obj = self.data_class(**entry)
            self.objects.append(obj)
            # Inject into index.
            for key, value in entry.items():
                # Lookups and searches are case insensitive. Normalize
                # here.
                value = value.lower()
                if key in self.no_index:
                    continue
                index = self.indices.setdefault(key, {})
                if value in index:
                    logger.debug(
                        '%s %r already taken in index %r and will be '
                        'ignored. This is an error in the databases.' %
                        (self.data_class_name, value, key))
                index[value] = obj

        self._is_loaded = True

    # Public API

    @lazy_load
    def __iter__(self):
        return iter(self.objects)

    @lazy_load
    def __len__(self):
        return len(self.objects)

    @lazy_load
    def get(self, **kw):
        kw.setdefault('default', None)
        default = kw.pop('default')
        if len(kw) != 1:
            raise TypeError('Only one criteria may be given')
        field, value = kw.popitem()
        if not isinstance(value, str):
            raise LookupError()
        # Normalize for case-insensitivity
        value = value.lower()
        index = self.indices[field]
        try:
            return index[value]
        except KeyError:
            # Pythonic APIs implementing     get() shouldn't raise KeyErrors.
            # Those are a bit unexpected and they should rather support
            # returning `None` by default and allow customization.
            return default

    @lazy_load
    def lookup(self, value):
        if not isinstance(value, str):
            raise LookupError()

        # Normalize for case-insensitivity
        value = value.lower()

        # Use indexes first
        for key in self.indices:
            try:
                return self.indices[key][value]
            except LookupError:
                pass

        # Use non-indexed values now. Avoid going through indexed values.
        for candidate in self:
            for k in self.no_index:
                v = candidate._fields.get(k)
                if v is None:
                    continue
                if v.lower() == value:
                    return candidate

        raise LookupError('Could not find a record for %r' % value)
