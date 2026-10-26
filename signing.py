# Eclipse Covenant MCP
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
# Herald-shaped signing, simulator mode (HMAC; P0 synthetic only).

"""Herald signer stub in simulator mode.

P0 uses HMAC-SHA256 with a labeled simulator key so records and packs
carry the Herald signature *shape* (offline-verifiable, key-bound)
without any live key material. This is NOT a production Herald key.
"""
import hashlib
import hmac
import json
import os

SIMULATOR_KEY = os.environ.get(
    "ECLIPSE_COVENANT_HERALD_KEY", "SIMULATOR-ONLY-HERALD-KEY-NOT-PRODUCTION"
).encode("utf-8")
KEY_MODE = "simulator"


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def content_hash(obj) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def sign(obj) -> str:
    return hmac.new(SIMULATOR_KEY, canonical(obj), hashlib.sha256).hexdigest()


def verify(obj, signature) -> bool:
    if not isinstance(signature, str):
        return False
    return hmac.compare_digest(sign(obj), signature)


def sign_approval(plan_id: str, approver: str) -> str:
    """Verified-approval pattern: approval is a signature over the
    plan id + approver identity, never a caller-supplied boolean."""
    return sign({"approval_for": plan_id, "approver": approver})


def verify_approval(plan_id: str, approver: str, signature) -> bool:
    if not isinstance(signature, str):
        return False
    return hmac.compare_digest(sign_approval(plan_id, approver), signature)

# Eclipse Covenant MCP · © 2026 Cyan Eclipse Technologies LLC · cetech.ai
