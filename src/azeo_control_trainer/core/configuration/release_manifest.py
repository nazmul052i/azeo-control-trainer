"""Transport-only release identities; usable without a database driver."""
import hashlib
import json


def plain(value):
    return json.loads(json.dumps(value, default=str, allow_nan=False))


def fingerprint(value):
    return hashlib.sha256(json.dumps(plain(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def deployable(obj):
    if obj["kind"] == "module":
        return "controller"
    if obj["kind"] == "display" and obj["path"].endswith("/draft.json"):
        return "station"
    return ""
