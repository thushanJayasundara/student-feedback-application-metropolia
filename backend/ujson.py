"""Standard-library fallback for environments that block ujson's native wheel."""

import json

JSONDecodeError = json.JSONDecodeError


def dumps(value, *args, **kwargs):
    return json.dumps(value, *args, **_json_kwargs(kwargs))


def loads(value, *args, **kwargs):
    return json.loads(value, *args, **_json_kwargs(kwargs))


def dump(value, file, *args, **kwargs):
    return json.dump(value, file, *args, **_json_kwargs(kwargs))


def load(file, *args, **kwargs):
    return json.load(file, *args, **_json_kwargs(kwargs))


def _json_kwargs(kwargs):
    supported = {"skipkeys", "ensure_ascii", "check_circular", "allow_nan", "cls", "indent", "separators", "default", "sort_keys", "object_hook", "parse_float", "parse_int", "parse_constant", "object_pairs_hook"}
    return {key: value for key, value in kwargs.items() if key in supported}
