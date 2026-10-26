# Eclipse Covenant MCP
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
# Core: Covenant Record vault + Covenant Gate + Covenant Pack + Verifier.

"""Eclipse Covenant — consent-bound lead work for real-estate agents:
every lead carries its provenance, every outreach is checked at send
time, and every session seals into the brokerage's defense file. It
does not sell leads, hold MLS data, or perform licensed acts.

P0: SYNTHETIC ONLY. No live outreach, no real leads, no real CRM or
DNC registry access. Send routes are simulator stubs that record,
never transmit. Counsel HARD GATE stands before any P1 live outreach.

DESIGN LAW (spec section 3): consent/revocation rules are VERSIONED
DATA parameters, never code constants. Every rule value read at
runtime comes from a RuleParameterSet JSON pack on disk; code reads
parameters, code never embeds them. Certificates are evidence,
never authorization.
"""
import copy
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import signing

HERE = Path(__file__).parent
PRODUCT = "Eclipse Covenant MCP"
CLASSES = ("CERTIFIED_CAPTURE", "SELF_CAPTURED", "VENDOR_ASSERTED",
           "LEGACY_UNVERIFIED")
# Monotone honesty order (spec 7.3): a record's class may only move
# UP this ladder via a verified-capture event, never down via intake.
_CLASS_RANK = {"LEGACY_UNVERIFIED": 0, "VENDOR_ASSERTED": 1,
               "SELF_CAPTURED": 2, "CERTIFIED_CAPTURE": 3}
CHANNELS = ("voice", "text", "email")
GRANT_BASES = ("PEWC", "EXPRESS", "EXISTING_RELATIONSHIP_PER_PARAMS")
# Vendor-lead posture is a CLOSED vocabulary (D8b): these are the only
# postures the Gate implements (spec 7.2). The values are mechanism
# identifiers selected at load/mandate time from this enum — the rule
# VALUES that govern behavior (which posture a pack/mandate carries)
# remain data in the pack/mandate, never code constants (spec L1).
VENDOR_POSTURES = ("NARROW_FIRST_CONTACT_ONLY", "REFUSE_ALL")


class ToolError(Exception):
    """A refusal by name. `refusal` is the verbatim refusal class."""

    def __init__(self, refusal, body=None, **extra):
        super().__init__(refusal)
        self.refusal = refusal
        self.body = body or ""
        self.extra = extra


# ------------------------------------------------------------ house guards
def coerce_int_param(value, name, refusal="INVALID_INT_PARAM"):
    """House guard: bool is not an int; digit strings coerce; the
    rest refuse by name instead of crashing or silently truncating."""
    if isinstance(value, bool):
        raise ToolError(refusal, f"{name} must be an integer, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    raise ToolError(refusal, f"{name} must be an integer, got {value!r}")


def parse_time(value, field="time"):
    """House guard: strict ISO-8601 parse; bad values refuse by name."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        raise ToolError("INVALID_TIME", f"{field} must be ISO-8601 text")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ToolError("INVALID_TIME", f"{field} is not ISO-8601: {value!r}")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def now_iso(at_time=None):
    dt = parse_time(at_time, "at_time") or datetime.now(timezone.utc)
    return dt.isoformat()


def require_ref(value, field):
    """Reference-type guard: ids are non-empty strings, never dicts,
    numbers, or caller-built objects."""
    if not isinstance(value, str) or not value.strip() or \
            any(ch.isspace() for ch in value):
        raise ToolError("INVALID_REFERENCE",
                        f"{field} must be a non-empty string reference")
    return value


def require_dict(value, field):
    if not isinstance(value, dict):
        raise ToolError("INVALID_ARGUMENT", f"{field} must be an object")
    return value


def require_str_list(value, field):
    """Reference-type guard for id lists: the value must be a list of
    strings. A bare int/str/dict is a malformed shape and refuses by
    name — never coerced, never iterated into a crash (house guard)."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ToolError("INVALID_ARGUMENT",
                        f"{field} must be a list of strings")
    out = []
    for item in value:
        if not isinstance(item, str):
            raise ToolError("INVALID_ARGUMENT",
                            f"{field} entries must be strings")
        out.append(item)
    return out


# Deep-key scan: nested denylist inputs are refused by name wherever
# they hide in the argument tree (house guard).
_DEEP_KEY_REFUSALS = (
    (("skip_checks", "skip_check", "bypass_gate", "bypass",
      "override_consent", "admin_override", "force_send"),
     "GATE_BYPASS_REFUSED"),
    (("mls_export", "listing_export", "listing_compilation"),
     "MLS_EXPORT_REFUSED"),
    (("export_leads", "export_list", "transfer_leads", "transfer_to",
      "share_with_brokerage", "sell_leads"), "LEAD_EXPORT_REFUSED"),
)


def deep_key_scan(node, path="args"):
    if isinstance(node, dict):
        for key, val in node.items():
            lk = str(key).lower()
            for names, refusal in _DEEP_KEY_REFUSALS:
                if lk in names:
                    raise ToolError(
                        refusal,
                        f"argument key {path}.{key} would effect a "
                        "permanently refused act (spec section 9)")
            deep_key_scan(val, f"{path}.{key}")
    elif isinstance(node, list):
        for i, val in enumerate(node):
            deep_key_scan(val, f"{path}[{i}]")


# ------------------------------------------------------------ rule packs
def _pack_unavailable(detail):
    raise ToolError(
        "RULE_PACK_UNAVAILABLE",
        f"rule parameter pack is structurally invalid: {detail} — "
        "fail closed, no rules in force")


def _req_pack_str(section, key, where):
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        _pack_unavailable(f"{where}.{key} must be a non-empty string")
    return value


def _req_pack_int(section, key, where):
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or \
            value < 0:
        _pack_unavailable(f"{where}.{key} must be a non-negative int")
    return value


def _req_pack_posture(section, key, where):
    """Closed-vocabulary posture check (D8b): the value must be a
    string AND a member of the implemented posture enum. A typo'd,
    lowercased, or invented posture is not a rule set — it refuses
    RULE_PACK_UNAVAILABLE at load rather than silently disabling
    posture enforcement at the Gate."""
    value = section.get(key)
    if not isinstance(value, str) or value not in VENDOR_POSTURES:
        _pack_unavailable(
            f"{where}.{key} must be one of the implemented "
            f"vendor-lead postures {list(VENDOR_POSTURES)}, "
            f"got {value!r}")
    return value


def _req_pack_str_list(section, key, where):
    value = section.get(key)
    if not isinstance(value, list) or \
            any(not isinstance(item, str) for item in value):
        _pack_unavailable(f"{where}.{key} must be a list of strings")
    return value


def _req_pack_section(pack, key):
    value = pack.get(key)
    if not isinstance(value, dict):
        _pack_unavailable(f"pack.{key} must be an object section")
    return value


def validate_rule_pack(pack):
    """Schema validation for a RuleParameterSet (D8). Every key the
    engine dereferences anywhere — revocation arithmetic, clarification
    template, revocation normalization vocabularies, posture defaults,
    DNC section, retention, vocabulary ref, staleness — is required
    here, with its type, AT LOAD. A structurally malformed pack (valid
    JSON object, missing/mistyped sections) is not a rule set: it
    refuses RULE_PACK_UNAVAILABLE before any intake/send/revocation
    path runs, and no pack-shape KeyError/AttributeError/TypeError can
    escape downstream. Absent posture data = pack unavailable, never
    posture=None/unlimited (fail-closed posture). D8b: posture VALUES
    are also validated here against the closed implemented enum —
    a present-but-unknown posture is as unavailable as an absent
    one."""
    if not isinstance(pack, dict):
        _pack_unavailable("pack is not a JSON object")
    for key in ("set_id", "version", "set_by", "effective_from",
                "pewc_definition_ref", "named_seller_scope_semantics",
                "legacy_posture",
                "fair_housing_vocabulary_version", "stale_after"):
        _req_pack_str(pack, key, "pack")
    # D8b: vendor_lead_posture_default is the one pack string the Gate
    # branches on exhaustively — closed vocabulary, not a free string.
    _req_pack_posture(pack, "vendor_lead_posture_default", "pack")
    try:
        datetime.fromisoformat(pack["stale_after"]).date()
    except ValueError:
        _pack_unavailable("pack.stale_after must be an ISO date")
    if not isinstance(pack.get("state_overlays"), dict):
        _pack_unavailable("pack.state_overlays must be an object")
    rev = _req_pack_section(pack, "revocation")
    _req_pack_int(rev, "honor_window_business_days", "revocation")
    for key in ("keyword_set", "plain_language_phrases",
                "all_channel_phrases", "reasonable_means"):
        _req_pack_str_list(rev, key, "revocation")
    channel_words = rev.get("channel_words")
    if not isinstance(channel_words, dict):
        _pack_unavailable("revocation.channel_words must be an object")
    for channel in CHANNELS:
        words = channel_words.get(channel)
        if not isinstance(words, list) or \
                any(not isinstance(w, str) for w in words):
            _pack_unavailable(
                f"revocation.channel_words.{channel} must be a list "
                "of strings")
    clar = rev.get("clarification")
    if not isinstance(clar, dict):
        _pack_unavailable("revocation.clarification must be an object")
    _req_pack_int(clar, "max_count", "revocation.clarification")
    _req_pack_int(clar, "window_minutes", "revocation.clarification")
    if not isinstance(clar.get("non_marketing_only"), bool):
        _pack_unavailable(
            "revocation.clarification.non_marketing_only must be a "
            "boolean")
    template = _req_pack_str(clar, "template",
                             "revocation.clarification")
    try:
        # The engine formats this template at clarification time; an
        # unformattable template is a load-time pack defect, never a
        # send-time KeyError.
        template.format(channel="text", seller="seller")
    except (KeyError, IndexError, ValueError):
        _pack_unavailable(
            "revocation.clarification.template is not formattable "
            "with {channel}/{seller}")
    revoke_all = rev.get("revoke_all")
    if not isinstance(revoke_all, dict):
        _pack_unavailable("revocation.revoke_all must be an object")
    _req_pack_str(revoke_all, "status", "revocation.revoke_all")
    _req_pack_str(revoke_all, "tail_rule_ref", "revocation.revoke_all")
    dnc = _req_pack_section(pack, "dnc")
    _req_pack_str_list(dnc, "registries", "dnc")
    _req_pack_str(dnc, "scrub_freshness", "dnc")
    retention = _req_pack_section(pack, "retention")
    _req_pack_int(retention, "consent_record_min_years", "retention")
    return pack


def load_rule_pack(state):
    """Load the active RuleParameterSet from its data pack on disk.

    Missing pack -> RULE_PACK_UNAVAILABLE (fail closed). Stale pack
    (past its data-carried stale_after, relative to the send time) ->
    RULE_PACK_STALE at gate time. Values are data, never constants.
    """
    cfg = state["_config"]
    path = Path(cfg["rule_pack_path"])
    if not path.exists():
        raise ToolError("RULE_PACK_UNAVAILABLE",
                        f"rule parameter pack not found: {path.name}")
    try:
        raw_pack = path.read_text()
    except OSError:
        raise ToolError("RULE_PACK_UNAVAILABLE",
                        f"rule parameter pack unreadable: {path.name}")
    try:
        pack = json.loads(raw_pack)
    except ValueError:
        # Corrupt pack: fail closed BY NAME (spec section 3 L1 posture);
        # a raw JSONDecodeError is never a surface behavior.
        raise ToolError("RULE_PACK_UNAVAILABLE",
                        f"rule parameter pack is corrupt (invalid "
                        f"JSON): {path.name} — no rules in force")
    if not isinstance(pack, dict):
        raise ToolError("RULE_PACK_UNAVAILABLE",
                        "rule parameter pack is not a JSON object — "
                        "fail closed, no rules in force")
    validate_rule_pack(pack)
    pack["_pack_hash"] = signing.content_hash(
        {k: v for k, v in pack.items() if not k.startswith("_")})
    pack["_ref"] = {"set_id": pack.get("set_id"),
                    "version": pack.get("version"),
                    "hash": pack["_pack_hash"],
                    "set_by": pack.get("set_by")}
    return pack


def load_rule_pack_by_ref(state, ref):
    """Verifier path: re-load the exact pinned version from the pack
    archive directory (rules then in force, spec L2)."""
    pack_dir = Path(state["_config"]["rule_pack_dir"])
    if not pack_dir.exists():
        return None
    for candidate in sorted(pack_dir.glob("*.json")):
        try:
            pack = json.loads(candidate.read_text())
        except (ValueError, OSError):
            continue
        if not isinstance(pack, dict):
            continue
        if pack.get("set_id") == ref.get("set_id") and \
                pack.get("version") == ref.get("version"):
            # Same load-time schema law as the active pack (D8): a
            # matched-but-malformed archive pack refuses by name, it
            # never reaches the verifier's revocation arithmetic.
            validate_rule_pack(pack)
            return pack
    return None


def pack_is_stale(pack, at_dt):
    stale_after = pack.get("stale_after")
    if not stale_after:
        return False
    return at_dt.date().isoformat() > stale_after


def add_business_days(start_dt, days):
    dt = start_dt
    remaining = days
    while remaining > 0:
        dt += timedelta(days=1)
        if dt.weekday() < 5:
            remaining -= 1
    return dt


# ------------------------------------------------------------ state
def empty_state(config=None):
    cfg = {
        "rule_pack_path": str(HERE / "data" / "rule_packs" /
                              "SYNTHETIC_DEFAULT-v1.json"),
        "rule_pack_dir": str(HERE / "data" / "rule_packs"),
        "vocab_path": str(HERE / "data" / "screen_vocabulary_v0.json"),
        "dnc_path": str(HERE / "data" / "dnc_fixture.json"),
        "cert_path": str(HERE / "fixtures" / "cert_fixtures.json"),
        "context_path": str(HERE / "data" / "context_fixtures.json"),
    }
    if config:
        cfg.update(config)
    return {"_config": cfg, "mandates": {}, "leads": {}, "records": {},
            "packs": {}, "plans": {}, "events": {},
            "read_audit": [], "operator_log": [],
            "counters": {"lead": 0, "record": 0, "pack": 0, "plan": 0,
                         "revocation": 0, "scrub": 0, "send": 0}}


def _next_id(state, kind, prefix):
    state["counters"][kind] += 1
    return f"{prefix}-{state['counters'][kind]:04d}"


def log_event(state, lead_id, kind, at, actor, detail):
    ev = {"at": at, "kind": kind, "actor": actor, "detail": detail}
    chain = state["events"].setdefault(lead_id, [])
    ev["prev_hash"] = chain[-1]["hash"] if chain else "GENESIS"
    ev["hash"] = signing.content_hash(
        {k: v for k, v in ev.items() if k != "hash"})
    chain.append(ev)
    return ev


# ------------------------------------------------------------ mandates
def mandate_create(state, args):
    """Operator surface (disjoint; server exposes it only when the
    operator surface is selected). Herald-signed by brokerage officer;
    no operator act touches a single send (spec 5.4)."""
    require_dict(args, "args")
    brokerage_id = require_ref(args.get("brokerage_id"), "brokerage_id")
    officer = require_ref(args.get("officer"), "officer")
    sig = args.get("officer_sig")
    expect = signing.sign({"mandate_officer": officer,
                           "brokerage_id": brokerage_id})
    if not isinstance(sig, str) or not signing.verify(
            {"mandate_officer": officer, "brokerage_id": brokerage_id},
            sig):
        raise ToolError("MANDATE_UNSIGNED",
                        "mandate requires a verified officer signature")
    pack = load_rule_pack(state)
    # D8b: a posture override is validated (type + closed vocabulary)
    # BEFORE the mandate is minted — an unknown override refuses by
    # name at creation and is never stored verbatim, where the Gate
    # would previously have fallen through it to unlimited sends.
    posture = args.get("vendor_lead_posture",
                       pack.get("vendor_lead_posture_default"))
    if not isinstance(posture, str) or posture not in VENDOR_POSTURES:
        raise ToolError(
            "INVALID_VENDOR_POSTURE",
            f"vendor_lead_posture must be one of the implemented "
            f"postures {list(VENDOR_POSTURES)}, got {posture!r} — "
            "mandate not created, override not stored")
    mid = f"MND-{len(state['mandates']) + 1:04d}"
    mandate = {
        "mandate_id": mid, "brokerage_id": brokerage_id,
        "broker_of_record": args.get("broker_of_record", brokerage_id),
        "officer": officer, "officer_sig": sig,
        "seats": require_str_list(args.get("seats"), "seats"),
        "channels": require_str_list(args.get("channels"), "channels")
        if args.get("channels") is not None else list(CHANNELS),
        "vendor_lead_posture": posture,
        "state_overlays": require_str_list(
            args.get("state_overlays"), "state_overlays"),
        "rule_version_pinned": pack["_ref"],
        "posture_history": [{"at": now_iso(args.get("at_time")),
                             "posture": posture}],
    }
    state["mandates"][mid] = mandate
    return {"mandate_id": mid, "rule_version": pack["_ref"],
            "vendor_lead_posture": mandate["vendor_lead_posture"]}


def get_mandate(state, mandate_id):
    require_ref(mandate_id, "mandate_id")
    mandate = state["mandates"].get(mandate_id)
    if mandate is None:
        raise ToolError("MANDATE_NOT_FOUND", f"no mandate {mandate_id}")
    return mandate


# ------------------------------------------------------------ certificates
def verify_certificate(state, ref, lead_key):
    """Fixture verifier (P0). Returns (status, detail): PASS only when
    the fixture registry says the cert verifies AND binds to this lead.
    Certificates are evidence, never authorization (spec L3)."""
    path = Path(state["_config"]["cert_path"])
    if not path.exists():
        return "UNVERIFIABLE_OFFLINE", "certificate registry unavailable"
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError):
        return "UNVERIFIABLE_OFFLINE", "certificate registry corrupt"
    registry = data.get("certificates", {}) if isinstance(data, dict) \
        else {}
    entry = registry.get(ref)
    if entry is None:
        return "FAIL", "certificate not found in fixture registry"
    if entry.get("verify_status") != "PASS":
        return "FAIL", entry.get("reason", "issuer verification failed")
    if entry.get("bound_lead_key") != lead_key:
        return "FAIL", "certificate binds to a different lead"
    return "PASS", "verified at intake (fixture verifier)"


def lead_key_of(identity):
    phones = identity.get("phones") or []
    if phones:
        return re.sub(r"\D", "", str(phones[0]))
    emails = identity.get("emails") or []
    return str(emails[0]).lower() if emails else "unknown"


def remainder_class(record):
    """Honest remainder after certificate failure (spec 7.3)."""
    if record.get("capture", {}).get("disclosure_text_verbatim") and \
            record.get("capture", {}).get("form_snapshot_ref"):
        return "SELF_CAPTURED"
    if record.get("source", {}).get("kind") in (
            "PORTAL_PROGRAM", "VENDOR_FEED", "CRM_IMPORT") and \
            record.get("source", {}).get("vendor_name"):
        return "VENDOR_ASSERTED"
    return "LEGACY_UNVERIFIED"


def _record_payload(record):
    return {k: v for k, v in record.items()
            if k not in ("hash", "herald_sig")}


def _seal_record(record, prev_hash):
    record["prev_hash"] = prev_hash
    record["hash"] = signing.content_hash(_record_payload(record))
    record["herald_sig"] = signing.sign(
        {"record_hash": record["hash"], "record_id": record["record_id"]})


# ------------------------------------------------------------ tool 1: intake
def lead_intake(state, args):
    require_dict(args, "args")
    deep_key_scan(args)
    mandate = get_mandate(state, args.get("mandate_id"))
    pack = load_rule_pack(state)
    at = now_iso(args.get("at_time"))
    identity = require_dict(args.get("identity") or {}, "identity")
    source = require_dict(args.get("source") or {}, "source")
    capture = require_dict(args.get("capture") or {}, "capture")
    for field in ("phones", "emails"):
        if identity.get(field) is not None and \
                not isinstance(identity.get(field), list):
            raise ToolError("INVALID_ARGUMENT",
                            f"identity.{field} must be a list")
    sellers = require_str_list(args.get("named_seller_set"),
                               "named_seller_set")
    key = lead_key_of(identity)

    # Duplicate under same mandate -> attach to existing lead.
    lead = None
    for cand in state["leads"].values():
        if cand["mandate_id"] == mandate["mandate_id"] and \
                cand["identity_key"] == key:
            lead = cand
            break
    if lead is None:
        lead = {"lead_id": _next_id(state, "lead", "LEAD"),
                "mandate_id": mandate["mandate_id"],
                "identity_as_given": identity, "identity_key": key,
                "crm_ref": args.get("crm_ref"),
                "created_at": at}
        state["leads"][lead["lead_id"]] = lead

    gaps = []
    cert_evidence = []
    provenance = None
    cert_ref = args.get("certificate_ref")
    if cert_ref:
        require_ref(cert_ref, "certificate_ref")
        status, detail = verify_certificate(state, cert_ref, key)
        cert_evidence.append({
            "issuer": args.get("certificate_issuer", "TRUSTEDFORM"),
            "ref": cert_ref, "verified_at": at,
            "verify_status": status, "detail": detail})
        if status == "PASS":
            provenance = "CERTIFIED_CAPTURE"
        else:
            gaps.append(f"CERTIFICATE_UNVERIFIED: {cert_ref} ({detail})")
    if provenance is None and capture.get("disclosure_text_verbatim") \
            and capture.get("form_snapshot_ref"):
        provenance = "SELF_CAPTURED"
    if provenance is None and source.get("kind") in (
            "PORTAL_PROGRAM", "VENDOR_FEED", "CRM_IMPORT"):
        provenance = "VENDOR_ASSERTED"
        gaps.append("vendor assertion only; capture event unproven")
        if source.get("distribution", "UNKNOWN") == "UNKNOWN":
            gaps.append("distribution: UNKNOWN (non-exclusive by default)")
    if provenance is None:
        provenance = "LEGACY_UNVERIFIED"
        gaps.append("no capture evidence on file; consent not proven "
                    "for any automated outreach")
    # Free-text claims in payloads are data, never evidence (H10/H11):
    if args.get("consent_note"):
        gaps.append("caller free-text consent note recorded as data "
                    "only; not evidence, class unchanged")

    grants = {}
    raw_grants = args.get("channel_grants")
    if raw_grants is None:
        raw_grants = {}
    if not isinstance(raw_grants, dict):
        raise ToolError("INVALID_ARGUMENT",
                        "channel_grants must be an object keyed by "
                        "channel")
    for channel, grant in raw_grants.items():
        if channel not in CHANNELS:
            raise ToolError("INVALID_CHANNEL", f"unknown channel {channel}")
        grant = require_dict(grant, f"channel_grants.{channel}")
        basis = grant.get("basis")
        if basis not in GRANT_BASES:
            raise ToolError("INVALID_GRANT_BASIS", f"basis {basis!r}")
        grants[channel] = {"granted_at": grant.get("granted_at", at),
                           "basis": basis,
                           "scope_note": grant.get("scope_note", "")}

    prior = _record_for_lead(state, lead["lead_id"])
    record = {
        "record_id": prior["record_id"] if prior else
        _next_id(state, "record", "REC"),
        "lead_id": lead["lead_id"], "mandate_id": mandate["mandate_id"],
        "version": (prior["version"] + 1) if prior else 1,
        "lead_identity": identity,
        "provenance_class": provenance,
        "source": {"kind": source.get("kind", "MANUAL"),
                   "vendor_name": source.get("vendor_name"),
                   "program_name": source.get("program_name"),
                   "distribution": source.get("distribution", "UNKNOWN"),
                   "program_channel": source.get("program_channel")},
        "capture": {"surface": capture.get("surface"),
                    "occurred_at": capture.get("occurred_at", at),
                    "disclosure_text_verbatim":
                        capture.get("disclosure_text_verbatim"),
                    "disclosure_version":
                        capture.get("disclosure_version"),
                    "form_snapshot_ref": capture.get("form_snapshot_ref")},
        "certificate_evidence": cert_evidence,
        "named_seller_set": sellers,
        "channel_grants": grants,
        "rule_version": pack["_ref"],
        "revocation_state": "NONE", "revocations": [],
        "dnc_history": [], "evidence_gaps": gaps,
        "created_at": prior["created_at"] if prior else at,
        "updated_at": at,
    }
    if prior is not None:
        # Duplicate intake ATTACHES (spec 5.1): the new version MERGES
        # onto the existing record — it never rebuilds from scratch.
        # Monotonic + history-preserving (spec 8.1: no transition
        # deletes history; spec 9: revocation is absolute):
        # - revocation state + revocation history are NEVER reset or
        #   deleted by a re-intake (duplicate-webhook idempotence);
        # - provenance class may only upgrade or stay, never downgrade
        #   (spec 7.3 monotone honesty);
        # - certificate evidence accumulates, never drops;
        # - stored capture evidence survives a bare duplicate whose
        #   payload omits it (field-wise merge, non-None wins);
        # - channel grants merge per channel; named sellers union;
        # - evidence gaps accumulate (deduped, order preserved).
        record["revocation_state"] = prior["revocation_state"]
        record["revocations"] = copy.deepcopy(prior["revocations"])
        record["dnc_history"] = copy.deepcopy(prior["dnc_history"])
        if _CLASS_RANK[prior["provenance_class"]] > \
                _CLASS_RANK[record["provenance_class"]]:
            record["provenance_class"] = prior["provenance_class"]
        merged_evidence = copy.deepcopy(prior["certificate_evidence"])
        for ev in cert_evidence:
            if ev not in merged_evidence:
                merged_evidence.append(ev)
        record["certificate_evidence"] = merged_evidence
        merged_capture = dict(prior["capture"])
        for field, value in record["capture"].items():
            if value is not None:
                merged_capture[field] = value
        record["capture"] = merged_capture
        merged_grants = copy.deepcopy(prior["channel_grants"])
        merged_grants.update(grants)
        record["channel_grants"] = merged_grants
        merged_sellers = list(prior["named_seller_set"])
        for seller in sellers:
            if seller not in merged_sellers:
                merged_sellers.append(seller)
        record["named_seller_set"] = merged_sellers
        merged_gaps = list(prior["evidence_gaps"])
        for gap in gaps:
            if gap not in merged_gaps:
                merged_gaps.append(gap)
        record["evidence_gaps"] = merged_gaps
        gaps = merged_gaps
        provenance = record["provenance_class"]
    _seal_record(record, prior["hash"] if prior else "GENESIS")
    state["records"][record["record_id"]] = record
    log_event(state, lead["lead_id"],
              "RECORD_ATTACHED" if prior else "RECORD_MINTED",
              at, "intake",
              {"record_id": record["record_id"],
               "provenance_class": provenance,
               "version": record["version"],
               "revocations_preserved": len(record["revocations"])})
    return {"lead_id": lead["lead_id"], "record_id": record["record_id"],
            "provenance_class": provenance,
            "evidence_gaps": gaps, "rule_version": pack["_ref"]}


def _record_for_lead(state, lead_id):
    for rec in state["records"].values():
        if rec["lead_id"] == lead_id:
            return rec
    return None


def _record_under_mandate(state, lead_id, mandate_id):
    rec = _record_for_lead(state, lead_id)
    if rec is None or rec["mandate_id"] != mandate_id:
        return None
    return rec


# ------------------------------------------------- tool 2: attach cert
def consent_attach_certificate(state, args):
    require_dict(args, "args")
    record_id = require_ref(args.get("record_id"), "record_id")
    record = state["records"].get(record_id)
    if record is None:
        raise ToolError("RECORD_NOT_FOUND", f"no record {record_id}")
    ref = args.get("trustedform_cert_url") or args.get("leadid_token")
    require_ref(ref, "certificate_ref")
    issuer = "TRUSTEDFORM" if args.get("trustedform_cert_url") else "JORNAYA"
    at = now_iso(args.get("at_time"))
    key = lead_key_of(record["lead_identity"])
    status, detail = verify_certificate(state, ref, key)
    record["certificate_evidence"].append(
        {"issuer": issuer, "ref": ref, "verified_at": at,
         "verify_status": status, "detail": detail})
    old_class = record["provenance_class"]
    if status == "PASS":
        record["provenance_class"] = "CERTIFIED_CAPTURE"
    else:
        record["provenance_class"] = remainder_class(record)
        note = f"CERTIFICATE_UNVERIFIED: {ref} ({detail})"
        record["evidence_gaps"].append(note)
        log_event(state, record["lead_id"], "CERTIFICATE_UNVERIFIED",
                  at, "consent_attach_certificate", {"note": note})
    record["version"] += 1
    record["updated_at"] = at
    _seal_record(record, record["hash"])
    log_event(state, record["lead_id"], "CLASS_REEVALUATED", at,
              "consent_attach_certificate",
              {"from": old_class, "to": record["provenance_class"],
               "verify_status": status})
    return {"record_id": record_id, "verify_status": status,
            "detail": detail,
            "provenance_class": record["provenance_class"]}


# ------------------------------------------------- tools 3-4: read/history
# Export detector (spec 5.3 / H8): rate + shape over record reads.
# A paginated sweep of single reads is an export shape, flagged and
# operator-logged even though each read is individually well-formed.
EXPORT_SWEEP_LIMIT = 20
EXPORT_SWEEP_WINDOW_SECONDS = 60


def _export_sweep_check(state, record_id):
    now = datetime.now(timezone.utc)
    audit = state.setdefault("read_audit", [])
    kept = []
    for entry in audit:
        try:
            at = parse_time(entry.get("at"))
        except ToolError:
            continue
        if at and (now - at).total_seconds() <= \
                EXPORT_SWEEP_WINDOW_SECONDS:
            kept.append(entry)
    audit[:] = kept
    if len(audit) >= EXPORT_SWEEP_LIMIT:
        state.setdefault("operator_log", []).append({
            "at": now.isoformat(), "kind": "EXPORT_SWEEP_DETECTED",
            "record_id": record_id, "reads_in_window": len(audit),
            "window_seconds": EXPORT_SWEEP_WINDOW_SECONDS})
        raise ToolError(
            "LEAD_EXPORT_REFUSED",
            f"record-read rate+shape matches a paginated export sweep "
            f"({len(audit)} reads inside "
            f"{EXPORT_SWEEP_WINDOW_SECONDS}s); flagged by the surface "
            "export detector (spec 5.3/H8) and operator-logged")
    audit.append({"record_id": record_id, "at": now.isoformat()})


def consent_record_read(state, args):
    require_dict(args, "args")
    deep_key_scan(args)
    # Bulk semantics are TYPE-STRICT: the string "true" is not a
    # boolean and is never silently honored NOR silently ignored —
    # a value that spells a bulk request ("true"/"yes"/"1") is a bulk
    # request shape and refuses by name; any other non-boolean value
    # is a malformed argument and refuses by name.
    for flag in ("bulk", "all_records"):
        value = args.get(flag)
        if value is None or value is False:
            continue
        if value is True or (isinstance(value, str) and
                             value.strip().lower() in
                             ("true", "yes", "1")):
            raise ToolError("LEAD_EXPORT_REFUSED",
                            "bulk record reads effect an export out "
                            "of the owning mandate (spec section 9)")
        raise ToolError("INVALID_ARGUMENT",
                        f"{flag} must be a boolean, got {value!r}")
    record_id = require_ref(args.get("record_id"), "record_id")
    record = state["records"].get(record_id)
    if record is None:
        raise ToolError("RECORD_NOT_FOUND", f"no record {record_id}")
    _export_sweep_check(state, record_id)
    return copy.deepcopy(record)


def consent_history(state, args):
    require_dict(args, "args")
    lead_id = require_ref(args.get("lead_id"), "lead_id")
    if lead_id not in state["leads"]:
        raise ToolError("LEAD_NOT_FOUND", f"no lead {lead_id}")
    return {"lead_id": lead_id,
            "events": copy.deepcopy(state["events"].get(lead_id, []))}


# ------------------------------------------------- tool 5: revocation
_STOP_BARE = None  # keywords live in the rule pack, never here


def _normalize_revocation(pack, payload_text, via_channel):
    """Normalize a revocation payload to a scope. Spec H4 / L5: a
    payload that matches no keyword or plain-language phrase is
    AMBIGUOUS, and ambiguous resolves to UNRESOLVED — an all-channel
    block — never to "not a revocation". This tool's input is a
    revocation presented for recording; fail-closed is the only
    reading the spec permits."""
    text = (payload_text or "").lower()
    rev = pack.get("revocation")
    if not isinstance(rev, dict):
        # Locally guarded (D8): load validation makes this unreachable
        # for packs loaded via load_rule_pack; a revocation must never
        # be lost to a pack-shape exception.
        raise ToolError("RULE_PACK_UNAVAILABLE",
                        "rule parameter pack revocation section is "
                        "missing or malformed — fail closed")
    matched = any(kw.lower() in text
                  for kw in rev.get("keyword_set", [])) or \
        any(p in text for p in rev.get("plain_language_phrases", []))
    if not matched:
        return "UNRESOLVED", None
    if any(p in text for p in rev.get("all_channel_phrases", [])):
        return "ALL_CHANNELS", None
    for channel, words in rev.get("channel_words", {}).items():
        if any(w in text for w in words):
            return "CHANNEL", channel
    return "UNRESOLVED", None


def revocation_record(state, args):
    require_dict(args, "args")
    lead_id = require_ref(args.get("lead_id"), "lead_id")
    lead = state["leads"].get(lead_id)
    if lead is None:
        raise ToolError("LEAD_NOT_FOUND", f"no lead {lead_id}")
    record = _record_for_lead(state, lead_id)
    if record is None:
        raise ToolError("CONSENT_RECORD_MISSING",
                        "revocation cannot bind: no consent record")
    pack = load_rule_pack(state)
    at_dt = parse_time(args.get("at_time")) or datetime.now(timezone.utc)
    at = at_dt.isoformat()
    raw = args.get("raw_payload")
    if not isinstance(raw, str) or not raw.strip():
        raise ToolError("INVALID_ARGUMENT",
                        "raw_payload (verbatim) is required")
    via = args.get("received_via_channel")
    if via not in CHANNELS:
        raise ToolError("INVALID_CHANNEL", f"received_via_channel {via!r}")
    scope, scope_channel = _normalize_revocation(pack, raw, via)
    window = coerce_int_param(
        pack["revocation"]["honor_window_business_days"],
        "honor_window_business_days")
    deadline = add_business_days(at_dt, window)
    entry = {
        "revocation_id": _next_id(state, "revocation", "REV"),
        "lead_id": lead_id, "received_at": at,
        "received_via_channel": via, "raw_payload_verbatim": raw,
        "normalized_scope": scope,
        "scope_channel": scope_channel or (via if scope == "CHANNEL"
                                           else None),
        "scope_rule_version": pack["_ref"],
        "honor_deadline": deadline.isoformat(),
        "honored_at": at,  # in-surface block is instant (spec 8.2)
    }
    record["revocations"].append(entry)
    if scope == "ALL_CHANNELS":
        record["revocation_state"] = "REVOKED_ALL"
    elif scope == "CHANNEL":
        record["revocation_state"] = "REVOKED_CHANNEL"
    else:
        record["revocation_state"] = "SCOPE_UNRESOLVED"
    record["version"] += 1
    record["updated_at"] = at
    _seal_record(record, record["hash"])
    log_event(state, lead_id, "REVOCATION_RECORDED", at, via,
              {"revocation_id": entry["revocation_id"], "scope": scope,
               "honor_deadline": entry["honor_deadline"]})
    _pack_append(state, record, at, "REVOCATION", None, None,
                 {"revocation": entry})
    result_clarification = None
    if args.get("send_clarification"):
        result_clarification = clarification_send(state, record, args)
    return {"revocation_id": entry["revocation_id"],
            "clarification_sent": result_clarification,
            "normalized_scope": scope,
            "scope_channel": entry["scope_channel"],
            "honor_deadline": entry["honor_deadline"],
            "blocked_effective": at,
            "note": "in-surface block is immediate; the honor clock is "
                    "the legal ceiling, never a grace window"}


def clarification_send(state, record, args):
    """System-generated, template-pinned, single-fire clarification
    (spec 8.2 / H9). No agent-authored clarification path exists."""
    pack = load_rule_pack(state)
    clar = pack["revocation"]["clarification"]
    if not isinstance(clar, dict):
        raise ToolError("RULE_PACK_UNAVAILABLE",
                        "rule parameter pack clarification section is "
                        "missing or malformed — fail closed")
    prior = [e for e in record["revocations"]
             if e.get("clarification_sent")]
    if len(prior) >= coerce_int_param(clar["max_count"], "max_count"):
        raise ToolError("CLARIFICATION_LIMIT_REACHED",
                        "the single permitted clarification has fired")
    rev = record["revocations"][-1]
    body = clar["template"].format(
        channel=rev["received_via_channel"],
        seller=record["mandate_id"])
    if args.get("clarification_body"):
        raise ToolError("CLARIFICATION_TEMPLATE_ONLY",
                        "clarification text is template-pinned; agent-"
                        "authored clarification content is refused")
    sent = {"at": now_iso(args.get("at_time")), "body": body,
            "template_pinned": True, "non_marketing": True}
    rev["clarification_sent"] = sent
    _pack_append(state, record, sent["at"], "CLARIFICATION", None, None,
                 {"clarification": sent})
    return sent

# Eclipse Covenant MCP · © 2026 Cyan Eclipse Technologies LLC · cetech.ai


# ------------------------------------------------------------ screens
def load_vocab(state):
    path = Path(state["_config"]["vocab_path"])
    if not path.exists():
        raise ToolError("VOCABULARY_UNAVAILABLE",
                        "screen vocabulary pack not found (fail closed)")
    try:
        vocab = json.loads(path.read_text())
    except (ValueError, OSError):
        raise ToolError("VOCABULARY_UNAVAILABLE",
                        "screen vocabulary pack is corrupt (fail closed)")
    if not isinstance(vocab, dict):
        raise ToolError("VOCABULARY_UNAVAILABLE",
                        "screen vocabulary pack is not an object "
                        "(fail closed)")
    return vocab


def copy_screen(state, args):
    """Tool 12: closed-vocabulary findings; text returned UNMODIFIED.
    Never silent rewrites (spec 6.1)."""
    require_dict(args, "args")
    text = args.get("text")
    if not isinstance(text, str):
        raise ToolError("INVALID_ARGUMENT", "text must be a string")
    vocab = load_vocab(state)
    findings = []
    low = text.lower()
    for cls in vocab["copy_classes"]:
        for pat in cls["patterns"]:
            idx = low.find(pat)
            if idx >= 0:
                findings.append({
                    "finding_id": f"F-{len(findings) + 1:03d}",
                    "tool": "COPY", "class": cls["class"],
                    "rule_ref": cls["rule_ref"],
                    "vocabulary_version": vocab["vocabulary_version"],
                    "span": {"start": idx, "end": idx + len(pat)},
                    "quoted": text[idx:idx + len(pat)]})
    return {"text": text, "text_unmodified": True,
            "text_hash": signing.content_hash(text),
            "findings": findings,
            "vocabulary_version": vocab["vocabulary_version"]}


def criteria_screen(state, args):
    """Tool 13: a screen, not a builder. Protected criteria refuse by
    name; known proxies refuse with the proxy named (spec 6.2)."""
    require_dict(args, "args")
    criteria = args.get("criteria")
    if not isinstance(criteria, str):
        raise ToolError("INVALID_ARGUMENT", "criteria must be a string")
    vocab = load_vocab(state)
    low = criteria.lower()
    refusals, findings = [], []
    for cls in vocab["criteria_protected"]:
        for pat in cls["patterns"]:
            if pat in low:
                findings.append({"tool": "CRITERIA",
                                 "class": cls["class"],
                                 "rule_ref": cls["rule_ref"],
                                 "matched": pat,
                                 "vocabulary_version":
                                     vocab["vocabulary_version"]})
                if "PROTECTED_CRITERIA_REFUSED" not in refusals:
                    refusals.append("PROTECTED_CRITERIA_REFUSED")
    for px in vocab["criteria_proxies"]:
        for pat in px["patterns"]:
            if pat in low:
                findings.append({"tool": "CRITERIA",
                                 "class": "PROXY_CRITERIA",
                                 "rule_ref": px["rule_ref"],
                                 "matched": pat, "proxy_named": px["proxy"],
                                 "vocabulary_version":
                                     vocab["vocabulary_version"]})
                if "PROXY_CRITERIA_REFUSED" not in refusals:
                    refusals.append("PROXY_CRITERIA_REFUSED")
    return {"criteria": criteria, "refused": bool(refusals),
            "refusals": refusals, "findings": findings,
            "audience_built": False,
            "note": "no partial audience is built on a refusal"}


# ------------------------------------------------------------ DNC scrub
def fresh_dnc_scrub(state, numbers, at):
    """Fresh scrub executed THIS send, loaded from disk at call time
    (spec 5.3 step 5). Results are single-send artifacts, never
    cached, never accepted from a caller."""
    path = Path(state["_config"]["dnc_path"])
    if not path.exists():
        raise ToolError("DNC_LIST_UNAVAILABLE",
                        "fixture DNC lists unavailable (fail closed)")
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError):
        raise ToolError("DNC_LIST_UNAVAILABLE",
                        "fixture DNC lists are corrupt (fail closed)")
    if not isinstance(data, dict) or \
            not isinstance(data.get("lists"), list):
        raise ToolError("DNC_LIST_UNAVAILABLE",
                        "fixture DNC lists are malformed (fail closed)")
    lists = [lst for lst in data["lists"] if isinstance(lst, dict)]
    per_number = []
    for num in numbers:
        digits = re.sub(r"\D", "", str(num))
        listed = any(digits == re.sub(r"\D", "", str(n))
                     for lst in lists
                     for n in (lst.get("numbers") or []))
        per_number.append({"number": num, "listed": listed})
    return {"scrub_id": _next_id(state, "scrub", "SCR"), "at": at,
            "trigger": "SEND",
            "lists": [{"registry": lst.get("registry"),
                       "list_version": lst.get("list_version")}
                      for lst in lists],
            "per_number": per_number}


# ------------------------------------------------------------ pack
def _pack_for(state, record):
    for pack in state["packs"].values():
        if pack["mandate_id"] == record["mandate_id"] and \
                pack["lead_id"] == record["lead_id"]:
            return pack
    mandate = state["mandates"][record["mandate_id"]]
    pack = {"pack_id": _next_id(state, "pack", "PACK"),
            "session_id": f"SES-{record['lead_id']}",
            "mandate_id": record["mandate_id"],
            "lead_id": record["lead_id"],
            "broker_of_record": mandate["broker_of_record"],
            "lead_record_hash": record["hash"],
            "provenance_class": record["provenance_class"],
            "entries": [], "revocations": [],
            "rule_versions_used": [], "scrub_refs": [],
            "prev_pack_hash": "GENESIS"}
    state["packs"][pack["pack_id"]] = pack
    return pack


def _seal_pack(pack):
    last = pack["entries"][-1]["entry_hash"] if pack["entries"] else \
        pack["prev_pack_hash"]
    pack["hash"] = signing.content_hash(
        {"pack_id": pack["pack_id"], "last_entry_hash": last,
         "entries": len(pack["entries"])})
    pack["herald_sig"] = signing.sign(
        {"pack_hash": pack["hash"], "pack_id": pack["pack_id"],
         "broker_of_record": pack["broker_of_record"]})
    pack["sealed_at"] = pack["entries"][-1]["at"] if pack["entries"] \
        else None


def _pack_append(state, record, at, kind, tool, channel, extra):
    pack = _pack_for(state, record)
    entry = {"at": at, "kind": kind, "tool": tool, "channel": channel}
    entry.update(extra or {})
    entry["prev_entry_hash"] = pack["entries"][-1]["entry_hash"] \
        if pack["entries"] else pack["prev_pack_hash"]
    entry["entry_hash"] = signing.content_hash(
        {k: v for k, v in entry.items() if k != "entry_hash"})
    pack["entries"].append(entry)
    if kind == "REVOCATION":
        pack["revocations"].append(extra["revocation"])
    rv = record["rule_version"]
    if rv not in pack["rule_versions_used"]:
        pack["rule_versions_used"].append(rv)
    pack["lead_record_hash"] = record["hash"]
    pack["provenance_class"] = record["provenance_class"]
    _seal_pack(pack)
    return pack


# ------------------------------------------------------------ THE GATE
_GATE_STEPS = ("RECORD_EXISTS", "CHANNEL_SCOPE", "NAMED_SELLER",
               "NO_OPEN_REVOCATION", "FRESH_DNC_SCRUB", "FAIR_HOUSING_GUARDS")


def run_gate(state, args, channel, body_text):
    """Send-time check pipeline, spec 5.3 order, short-circuiting.
    Returns (record, transcript) or seals a refusal and raises."""
    at_dt = parse_time(args.get("at_time")) or datetime.now(timezone.utc)
    at = at_dt.isoformat()
    lead_id = require_ref(args.get("lead_id"), "lead_id")
    mandate_id = require_ref(args.get("mandate_id"), "mandate_id")
    mandate = get_mandate(state, mandate_id)
    sender = require_ref(args.get("sending_identity"), "sending_identity")
    pack_rules = load_rule_pack(state)
    transcript = {"rule_version": pack_rules["_ref"], "steps": [],
                  "provenance_class": None, "channel": channel}

    def fail(step_idx, refusal, body, record=None):
        for i, name in enumerate(_GATE_STEPS):
            if i < step_idx:
                transcript["steps"].append({"step": name,
                                            "result": "PASS"})
            elif i == step_idx:
                transcript["steps"].append({"step": name,
                                            "result": refusal})
            else:
                transcript["steps"].append(
                    {"step": name, "result": "NOT_RUN_UPSTREAM_REFUSAL"})
        if record is not None:
            _pack_append(state, record, at, "REFUSAL",
                         f"outreach_{channel}", channel,
                         {"check_transcript": copy.deepcopy(transcript),
                          "refusal_class": refusal, "refusal_body": body,
                          "message_body_verbatim": body_text})
            log_event(state, record["lead_id"], "SEND_REFUSED", at,
                      sender, {"refusal": refusal, "channel": channel})
        raise ToolError(refusal, body)

    # Step 0 (additional, spec 5.3 tail): mandate covers the sender.
    if sender not in (mandate.get("seats") or []):
        rec_any = _record_for_lead(state, lead_id)
        if rec_any is not None:
            _pack_append(state, rec_any, at, "REFUSAL",
                         f"outreach_{channel}", channel,
                         {"refusal_class": "MANDATE_MISMATCH",
                          "refusal_body": "sending identity is not a "
                           "seat on the presented mandate",
                          "message_body_verbatim": body_text})
        raise ToolError("MANDATE_MISMATCH",
                        "sending identity is not covered by the "
                        "presented mandate (identity comes from the "
                        "mandate seat, never a caller-supplied claim)")

    # Step 1: record exists under THIS mandate.
    record = _record_under_mandate(state, lead_id, mandate_id)
    if record is None:
        raise ToolError("CONSENT_RECORD_MISSING",
                        "no consent record for this lead under this "
                        "brokerage's mandate")
    transcript["provenance_class"] = record["provenance_class"]
    if record["provenance_class"] not in CLASSES:
        # Class sweep (D8b class): a class value the engine never
        # mints (e.g. arriving via restored/tampered state) branches
        # nowhere — fail closed instead of falling through every
        # class branch to the send.
        fail(0, "CONSENT_RECORD_MISSING",
             f"record carries unrecognized provenance class "
             f"{record['provenance_class']!r} — fail closed, no "
             "send proceeds on a class outside the closed "
             "vocabulary", record)
    if record["provenance_class"] == "LEGACY_UNVERIFIED":
        fail(0, "CONSENT_RECORD_MISSING",
             "record exists but class LEGACY_UNVERIFIED posts "
             f"{pack_rules.get('legacy_posture')}: evidence gaps = "
             f"{record['evidence_gaps']} — an assumed consent is an "
             "absent consent", record)
    if pack_is_stale(pack_rules, at_dt):
        fail(0, "RULE_PACK_STALE",
             "active rule parameter pack is past its data-carried "
             "stale_after date; sends fail closed until a current "
             "pack is pinned", record)

    # Step 2: channel scope. Voice consent is not text consent.
    grant = record["channel_grants"].get(channel)
    if grant is None:
        granted = sorted(record["channel_grants"].keys()) or ["none"]
        fail(1, "CONSENT_SCOPE_MISMATCH",
             f"no {channel} grant on this record; granted channels: "
             f"{', '.join(granted)}. Grants are per-channel: voice "
             "consent is not text consent", record)

    # Step 3: named-seller coverage.
    if sender not in (record.get("named_seller_set") or []):
        fail(2, "CONSENT_SELLER_MISMATCH",
             "sending brokerage/agent is not in the disclosure's "
             "named-seller set for this record", record)

    # Step 4: no open revocation (unresolved scope blocks ALL).
    rstate = record["revocation_state"]
    if rstate not in ("NONE", "REVOKED_CHANNEL", "REVOKED_ALL",
                      "SCOPE_UNRESOLVED"):
        # Class sweep (D8b class): an unrecognized revocation state
        # branches nowhere — treat as an unresolved block, never as
        # "no revocation".
        fail(3, "CONSENT_REVOKED",
             f"record carries unrecognized revocation state "
             f"{rstate!r} — fail closed, all channels blocked until "
             "the state is a recognized value", record)
    if rstate == "REVOKED_ALL":
        fail(3, "CONSENT_REVOKED",
             "an all-channels revocation is open on this record", record)
    if rstate == "SCOPE_UNRESOLVED":
        fail(3, "CONSENT_REVOKED",
             "a revocation with UNRESOLVED scope blocks all channels "
             "until scope is resolved (spec L5)", record)
    if rstate == "REVOKED_CHANNEL":
        for rev in record["revocations"]:
            # Anything that is not a clean CHANNEL-scoped revocation
            # for a DIFFERENT channel blocks this send (spec L5):
            # ALL_CHANNELS, UNRESOLVED, and unrecognized scope values
            # alike (D8b class sweep — no fall-through on scope).
            if rev.get("normalized_scope") != "CHANNEL" or \
                    rev.get("scope_channel") == channel:
                fail(3, "CONSENT_REVOKED",
                     f"channel {channel} is revoked on this record",
                     record)

    # Vendor posture (mandate data): narrow first contact only.
    if record["provenance_class"] == "VENDOR_ASSERTED":
        posture = mandate.get("vendor_lead_posture") or \
            pack_rules.get("vendor_lead_posture_default")
        if posture == "REFUSE_ALL":
            fail(1, "CONSENT_RECORD_MISSING",
                 "mandate vendor-lead posture REFUSE_ALL: vendor-"
                 "asserted leads are not contacted under this mandate",
                 record)
        elif posture == "NARROW_FIRST_CONTACT_ONLY":
            program_channel = record["source"].get("program_channel")
            prior_sends = sum(
                1 for p in state["packs"].values()
                for e in p["entries"]
                if e["kind"] == "SEND" and p["lead_id"] == lead_id)
            if prior_sends >= 1:
                fail(1, "VENDOR_POSTURE_EXHAUSTED",
                     "NARROW_FIRST_CONTACT_ONLY: the single first-"
                     "contact attempt for this VENDOR_ASSERTED lead "
                     "has been used", record)
            if program_channel and program_channel != channel:
                fail(1, "VENDOR_POSTURE_EXHAUSTED",
                     f"NARROW_FIRST_CONTACT_ONLY: first contact must "
                     f"use the program-named channel ({program_channel})",
                     record)
        else:
            # D8b defense in depth: EVERY posture branch is closed.
            # An unrecognized posture (typo, case variant, invented
            # value, non-string) reaching the Gate — via restored
            # state or any path that bypassed load/mandate validation
            # — refuses BY NAME; it never falls through to an
            # unpostured (unlimited) send.
            fail(1, "VENDOR_POSTURE_UNKNOWN",
                 f"vendor-lead posture {posture!r} is not an "
                 f"implemented posture {list(VENDOR_POSTURES)} — "
                 "fail closed, no vendor-asserted send proceeds "
                 "under an unrecognized posture", record)

    # Step 5: fresh DNC scrub at SEND.
    scrub = None
    if channel in ("voice", "text"):
        numbers = record["lead_identity"].get("phones") or []
        scrub = fresh_dnc_scrub(state, numbers, at)
        record["dnc_history"].append(scrub)
        transcript["scrub"] = {"scrub_id": scrub["scrub_id"],
                                "at": scrub["at"],
                                "lists": scrub["lists"]}
        listed = [p for p in scrub["per_number"] if p["listed"]]
        if listed and grant.get("basis") != "PEWC":
            fail(4, "DNC_LISTED",
                 f"number {listed[0]['number']} is on a DNC registry "
                 f"per fresh send-time scrub {scrub['scrub_id']} and "
                 "the channel grant carries no PEWC consent authority",
                 record)
    else:
        transcript["scrub"] = {"result": "NOT_APPLICABLE_CHANNEL",
                               "note": "email is not a registry channel"}

    # Step 6: fair-housing guards on the send payload.
    findings = []
    if body_text:
        findings = copy_screen(state, {"text": body_text})["findings"]
    criteria = args.get("audience_criteria")
    crit_refusals = []
    if criteria:
        crit = criteria_screen(state, {"criteria": criteria})
        findings += crit["findings"]
        crit_refusals = crit["refusals"]
    transcript["guard_findings"] = findings
    if crit_refusals:
        fail(5, crit_refusals[0],
             f"criteria screen refused: {crit_refusals} "
             f"findings={findings}", record)
    if findings:
        fail(5, "COPY_SCREEN_FINDING",
             f"copy screen findings block this send: {findings}", record)

    for name in _GATE_STEPS:
        transcript["steps"].append({"step": name, "result": "PASS"})
    transcript["scrub_ref"] = scrub["scrub_id"] if scrub else None
    return record, transcript


def _send(state, args, channel, body_text):
    deep_key_scan(args)
    record, transcript = run_gate(state, args, channel, body_text)
    at = transcript and now_iso(args.get("at_time"))
    send_id = _next_id(state, "send", "SEND")
    # SIMULATOR ROUTE (P0): records the send, never transmits.
    receipt = {"send_id": send_id, "status": "SENT_SIMULATOR",
               "route": "SIMULATOR_STUB — no transmission occurred",
               "channel": channel, "lead_id": record["lead_id"],
               "provenance_class": record["provenance_class"],
               "at": at, "check_transcript": transcript}
    _pack_append(state, record, at, "SEND", f"outreach_{channel}",
                 channel, {"check_transcript": transcript,
                           "message_body_verbatim": body_text,
                           "send_id": send_id})
    log_event(state, record["lead_id"], "SEND_EXECUTED", at,
              args.get("sending_identity"),
              {"send_id": send_id, "channel": channel})
    return receipt


def outreach_text(state, args):
    require_dict(args, "args")
    return _send(state, args, "text", args.get("body"))


def outreach_call_place(state, args):
    """P0 dialer is a STUB (spec C1): Follow Up Boss's API does not
    place calls; live voice routes via a provider at P1."""
    require_dict(args, "args")
    receipt = _send(state, args, "voice",
                    args.get("call_purpose_class"))
    receipt["route"] = "SIMULATOR_DIALER_STUB — FUB API does not " \
                       "place calls; no call was placed"
    return receipt


def outreach_email(state, args):
    require_dict(args, "args")
    body = f"{args.get('subject', '')}\n{args.get('body', '')}"
    return _send(state, args, "email", body)


# ------------------------------------------------- tool 6: context read
def lead_context_read(state, args):
    """Fixture context only at P0 (spec C2). The Gate binds contact,
    not knowledge: works on leads with no consent record."""
    require_dict(args, "args")
    lead_id = require_ref(args.get("lead_id"), "lead_id")
    if lead_id not in state["leads"]:
        raise ToolError("LEAD_NOT_FOUND", f"no lead {lead_id}")
    try:
        data = json.loads(
            Path(state["_config"]["context_path"]).read_text())
    except (ValueError, OSError):
        raise ToolError("CONTEXT_UNAVAILABLE",
                        "fixture context is corrupt or unreadable "
                        "(fail closed)")
    if not isinstance(data, dict) or \
            not isinstance(data.get("contexts"), dict):
        raise ToolError("CONTEXT_UNAVAILABLE",
                        "fixture context is malformed (fail closed)")
    ctx = copy.deepcopy(data["contexts"]["default"])
    return {"lead_id": lead_id, "synthetic": True,
            "label": "SYNTHETIC — fixture context only (P0)",
            "context": ctx,
            "note": "enrichment is not outreach authorization; this "
                    "read creates no consent record"}


# ------------------------------------------------- tool 10: sequences
def followup_sequence_plan(state, args):
    """Modes: create (default) | approve | run_step. Plan approval is
    never send authorization: each step re-gates at its send time."""
    require_dict(args, "args")
    deep_key_scan(args)
    mode = args.get("mode", "create")
    if mode == "create":
        lead_id = require_ref(args.get("lead_id"), "lead_id")
        if lead_id not in state["leads"]:
            raise ToolError("LEAD_NOT_FOUND", f"no lead {lead_id}")
        steps = args.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ToolError("INVALID_ARGUMENT", "steps[] is required")
        if len(steps) > 50:
            raise ToolError("SEQUENCE_SIZE_REFUSED",
                            "oversized plans are a de-facto blast "
                            "(spec H6); cap is 50 steps")
        for st in steps:
            require_dict(st, "step")
            if st.get("channel") not in CHANNELS:
                raise ToolError("INVALID_CHANNEL",
                                f"step channel {st.get('channel')!r}")
        plan = {"plan_id": _next_id(state, "plan", "PLAN"),
                "lead_id": lead_id,
                "mandate_id": args.get("mandate_id"),
                "steps": [{"channel": s["channel"],
                           "body": s.get("body", ""),
                           "offset_hours": s.get("offset_hours", 0),
                           "rendered_as": "DRAFT"} for s in steps],
                "approval": None, "state": "PENDING_APPROVAL",
                "on_refusal": args.get("on_refusal", "HALT"),
                "created_at": now_iso(args.get("at_time"))}
        state["plans"][plan["plan_id"]] = plan
        return {"plan_id": plan["plan_id"], "state": plan["state"],
                "steps_rendered_as": "DRAFT"}
    plan_id = require_ref(args.get("plan_id"), "plan_id")
    plan = state["plans"].get(plan_id)
    if plan is None:
        raise ToolError("PLAN_NOT_FOUND", f"no plan {plan_id}")
    if mode == "approve":
        approver = require_ref(args.get("approver"), "approver")
        if not signing.verify_approval(plan_id, approver,
                                       args.get("approval_sig")):
            raise ToolError("APPROVAL_UNVERIFIED",
                            "plan approval requires a verified "
                            "signature over plan_id + approver")
        plan["approval"] = {"by": approver,
                            "at": now_iso(args.get("at_time"))}
        plan["state"] = "APPROVED"
        return {"plan_id": plan_id, "state": "APPROVED",
                "note": "approval is not send authorization; each "
                        "step re-gates at its own send time"}
    if mode == "run_step":
        idx = coerce_int_param(args.get("step_index"), "step_index")
        if idx < 0 or idx >= len(plan["steps"]):
            raise ToolError("INVALID_ARGUMENT", "step_index out of range")
        step = plan["steps"][idx]
        if args.get("body") is not None and args["body"] != step["body"]:
            plan["state"] = "PENDING_APPROVAL"
            plan["approval"] = None
            raise ToolError("SEQUENCE_STEP_UNAPPROVED",
                            "step content was edited after approval; "
                            "the plan re-opened and this step is "
                            "unapproved")
        if plan["state"] != "APPROVED" or not plan["approval"]:
            record = _record_for_lead(state, plan["lead_id"])
            if record is not None:
                _pack_append(state, record, now_iso(args.get("at_time")),
                             "REFUSAL", "followup_sequence_plan",
                             step["channel"],
                             {"refusal_class": "SEQUENCE_STEP_UNAPPROVED",
                              "refusal_body": "step attempted without a "
                               "human approval record on the plan"})
            raise ToolError("SEQUENCE_STEP_UNAPPROVED",
                            "no human approval record on this plan")
        send_args = {"lead_id": plan["lead_id"],
                     "mandate_id": plan["mandate_id"],
                     "sending_identity": args.get("sending_identity"),
                     "at_time": args.get("at_time")}
        try:
            receipt = _send(state, send_args, step["channel"],
                            step["body"])
        except ToolError:
            if plan["on_refusal"] != "CONTINUE":
                plan["state"] = "HALTED"
            raise
        return {"plan_id": plan_id, "step_index": idx,
                "step_state": "SENT", "receipt": receipt}
    raise ToolError("INVALID_ARGUMENT", f"unknown mode {mode!r}")


# ------------------------------------------------- tool 11: showing
_LICENSED_ACT_MARKERS = ("negotiat", "counteroffer", "opinion of value",
                         "apprais", "contract term", "price advice",
                         "offer price", "legal advice")


def showing_schedule(state, args):
    """Clerical only: logistics yes, terms no (spec 5.2 / section 9).
    Scheduling never sends a message itself; a scheduling message
    goes through tools 7-9 and the Gate."""
    require_dict(args, "args")
    deep_key_scan(args)
    lead_id = require_ref(args.get("lead_id"), "lead_id")
    if lead_id not in state["leads"]:
        raise ToolError("LEAD_NOT_FOUND", f"no lead {lead_id}")
    payload = json.dumps(args.get("logistics") or {}).lower()
    for marker in _LICENSED_ACT_MARKERS:
        if marker in payload:
            raise ToolError("LICENSED_ACT_REFUSED",
                            f"logistics payload contains licensed-act "
                            f"content ({marker!r}): negotiation, price "
                            "advice, and contract terms route to the "
                            "licensee, never this tool")
    return {"lead_id": lead_id, "status": "SCHEDULED_CLERICAL",
            "window": args.get("window"),
            "note": "scheduling is not outreach consent; any message "
                    "about this showing must go through the Gate"}


# ------------------------------------------------- tool 14: verifier
def consent_record_verify(state, args):
    """Offline verifier (separate code path from the sealer).
    Modes RECORD / PACK. Enumerated discrepancies, never a score."""
    require_dict(args, "args")
    mode = args.get("mode")
    if mode == "RECORD":
        supplied = args.get("record")
        if supplied is not None and not isinstance(supplied, dict):
            raise ToolError("INVALID_ARGUMENT",
                            "record must be an object")
        record_id = args.get("record_id", "")
        if record_id and not isinstance(record_id, str):
            raise ToolError("INVALID_ARGUMENT",
                            "record_id must be a string reference")
        record = supplied or state["records"].get(record_id)
        if record is None:
            return {"verdict": "INCOMPLETE",
                    "missing": ["record not found"]}
        problems = []
        expect_hash = signing.content_hash(_record_payload(record))
        if expect_hash != record.get("hash"):
            problems.append("record hash mismatch (tampered or corrupt)")
        if not signing.verify({"record_hash": record.get("hash"),
                               "record_id": record.get("record_id")},
                              record.get("herald_sig")):
            problems.append("Herald signature does not verify")
        cls = record.get("provenance_class")
        if cls not in CLASSES:
            problems.append(f"unknown provenance class {cls!r}")
        cert_evidence = record.get("certificate_evidence", [])
        if not isinstance(cert_evidence, list):
            problems.append("certificate_evidence is not a list")
            cert_evidence = []
        capture = record.get("capture")
        if not isinstance(capture, dict):
            capture = {}
        if cls == "CERTIFIED_CAPTURE":
            ok = any(isinstance(e, dict) and
                     e.get("verify_status") == "PASS"
                     for e in cert_evidence)
            offline = Path(state["_config"]["cert_path"]).exists()
            if not ok and offline:
                problems.append("class CERTIFIED_CAPTURE without a "
                                "verifying certificate (class exceeds "
                                "its verified evidence)")
            elif not ok:
                problems.append("UNVERIFIABLE_OFFLINE: certificate "
                                "refs cannot be re-verified offline")
        if cls == "SELF_CAPTURED" and not (
                capture.get("disclosure_text_verbatim")
                and capture.get("form_snapshot_ref")):
            problems.append("class SELF_CAPTURED without stored form + "
                            "disclosure (class exceeds its evidence)")
        return {"verdict": "FAILED" if problems else "VERIFIED",
                "discrepancies": problems, "missing": []}
    if mode == "PACK":
        supplied_pack = args.get("pack")
        if supplied_pack is not None and \
                not isinstance(supplied_pack, dict):
            raise ToolError("INVALID_ARGUMENT",
                            "pack must be an object")
        pack_id = args.get("pack_id", "")
        if pack_id and not isinstance(pack_id, str):
            raise ToolError("INVALID_ARGUMENT",
                            "pack_id must be a string reference")
        pack = supplied_pack or state["packs"].get(pack_id)
        if pack is None:
            return {"verdict": "INCOMPLETE", "missing": ["pack not found"]}
        problems, missing = [], []
        entries = pack.get("entries", [])
        if not isinstance(entries, list):
            problems.append("pack entries is not a list")
            entries = []
        prev = pack.get("prev_pack_hash", "GENESIS")
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                problems.append(f"entry {i}: not an object")
                continue
            if entry.get("prev_entry_hash") != prev:
                problems.append(f"entry {i}: chain break")
            expect = signing.content_hash(
                {k: v for k, v in entry.items() if k != "entry_hash"})
            if expect != entry.get("entry_hash"):
                problems.append(f"entry {i}: hash mismatch (tampered)")
            prev = entry.get("entry_hash")
            if entry.get("kind") == "SEND":
                tr = entry.get("check_transcript")
                if not isinstance(tr, dict):
                    tr = {}
                steps = tr.get("steps")
                if not isinstance(steps, list):
                    steps = []
                if len(steps) != len(_GATE_STEPS):
                    problems.append(f"entry {i}: SEND without a full "
                                    "6-step check transcript")
                elif any(not isinstance(s, dict) or
                         s.get("result") != "PASS" for s in steps):
                    problems.append(f"entry {i}: SEND transcript "
                                    "contains a non-PASS step")
            if entry.get("kind") == "REFUSAL" and \
                    not entry.get("refusal_class"):
                problems.append(f"entry {i}: REFUSAL without a named "
                                "refusal class")
        last = entries[-1].get("entry_hash") if entries \
            else pack.get("prev_pack_hash")
        expect_pack = signing.content_hash(
            {"pack_id": pack.get("pack_id"), "last_entry_hash": last,
             "entries": len(entries)})
        if expect_pack != pack.get("hash"):
            problems.append("pack seal hash mismatch")
        if not signing.verify({"pack_hash": pack.get("hash"),
                               "pack_id": pack.get("pack_id"),
                               "broker_of_record":
                                   pack.get("broker_of_record")},
                              pack.get("herald_sig")):
            problems.append("pack Herald signature does not verify")

        def check_pinned(ref, where):
            """L2 pinned-hash comparison: the archive pack that the
            pinned ref resolves to must hash to EXACTLY the pinned
            rule_version.hash. set_id+version match alone is not
            verification — a post-seal content edit fails here."""
            pinned = load_rule_pack_by_ref(state, ref)
            if pinned is None:
                missing.append(f"rule pack {ref.get('set_id')}/"
                               f"{ref.get('version')} not in archive")
                return None
            if not ref.get("hash"):
                missing.append(f"{where}: rule_version ref carries "
                               "no pinned hash")
                return pinned
            actual = signing.content_hash(
                {k: v for k, v in pinned.items()
                 if not k.startswith("_")})
            if actual != ref.get("hash"):
                problems.append(
                    f"{where}: archive rule pack content hash "
                    f"{actual[:12]}.. does not match the pinned "
                    f"rule_version.hash {str(ref.get('hash'))[:12]}.. "
                    "(pinned-hash comparison, spec L2)")
            return pinned

        for ref in pack.get("rule_versions_used", []):
            if isinstance(ref, dict):
                check_pinned(ref, "rule_versions_used")
        # Rule arithmetic re-checked under the pinned version (L2).
        revocations = pack.get("revocations", [])
        if not isinstance(revocations, list):
            problems.append("pack revocations is not a list")
            revocations = []
        for rev in revocations:
            if not isinstance(rev, dict):
                problems.append("revocation entry is not an object")
                continue
            ref = rev.get("scope_rule_version") or {}
            if not isinstance(ref, dict):
                problems.append("revocation scope_rule_version is "
                                "not an object")
                continue
            pinned = check_pinned(ref, "revocation")
            if pinned is None:
                continue
            if not isinstance(rev.get("received_at"), str):
                problems.append("revocation entry carries no "
                                "received_at timestamp")
                continue
            window = pinned["revocation"]["honor_window_business_days"]
            expect_deadline = add_business_days(
                parse_time(rev["received_at"]), window).isoformat()
            if expect_deadline != rev.get("honor_deadline"):
                problems.append("revocation honor deadline disagrees "
                                "with the pinned rule-version arithmetic")
        verdict = "FAILED" if problems else (
            "INCOMPLETE" if missing else "VERIFIED")
        return {"verdict": verdict, "discrepancies": problems,
                "missing": missing}
    raise ToolError("INVALID_ARGUMENT", "mode must be RECORD or PACK")


# ------------------------------------------------------------ dispatch
TOOLS = {
    "lead_intake": lead_intake,
    "consent_attach_certificate": consent_attach_certificate,
    "consent_record_read": consent_record_read,
    "consent_history": consent_history,
    "revocation_record": revocation_record,
    "lead_context_read": lead_context_read,
    "outreach_text": outreach_text,
    "outreach_call_place": outreach_call_place,
    "outreach_email": outreach_email,
    "followup_sequence_plan": followup_sequence_plan,
    "showing_schedule": showing_schedule,
    "copy_screen": copy_screen,
    "criteria_screen": criteria_screen,
    "consent_record_verify": consent_record_verify,
}

OPERATOR_TOOLS = {"mandate_create": mandate_create}


def call_tool(state, name, args):
    fn = TOOLS.get(name) or OPERATOR_TOOLS.get(name)
    if fn is None:
        raise ToolError("UNKNOWN_TOOL", f"no such tool: {name}")
    return fn(state, args or {})

# Eclipse Covenant MCP · © 2026 Cyan Eclipse Technologies LLC · cetech.ai
