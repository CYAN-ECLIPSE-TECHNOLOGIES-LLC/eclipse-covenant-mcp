# Eclipse Covenant MCP
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
# Fix-round regressions D8: Verity re-bench shapes replicated
# BYTE-FOR-BYTE from the retained re-bench scorecard
# (eclipse-covenant-mcp-2026-10-25-2.json, PARTIAL 44/45, D8 HIGH).

"""SYNTHETIC ONLY — all leads, numbers (555-01XX), and consent events
in this suite are fixtures. No live sends, no real data."""
import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))

import covenant_engine as eng  # noqa: E402
import signing  # noqa: E402
from covenant_engine import ToolError  # noqa: E402

T0 = "2026-10-26T09:00:00+00:00"
SELLER = "seller-alpha"
GOOD_PACK = json.loads((HERE / "data" / "rule_packs" /
                        "SYNTHETIC_DEFAULT-v1.json").read_text())

# Verity D8 surviving probe, byte-for-byte: valid JSON object, no
# revocation section (and no posture/DNC/vocab sections either).
MINIMAL_PACK = {"set_id": "X", "version": "v9",
                "set_by": "SYNTHETIC_DEFAULT"}


def officer_sig(b="brokerage-1", o="officer-1"):
    return signing.sign({"mandate_officer": o, "brokerage_id": b})


def expect_refusal(fn):
    try:
        out = fn()
        return ("NO_REFUSAL", out)
    except ToolError as e:
        return (e.refusal, e.body)
    except Exception as e:  # raw escape = the defect under test
        return (f"RAW:{type(e).__name__}", str(e)[:200])


class D8Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dnc = self.tmp / "dnc.json"
        shutil.copy(HERE / "data" / "dnc_fixture.json", self.dnc)

    def state_with_pack(self, pack_obj=None, raw_text=None):
        pack_path = self.tmp / f"pack-{len(list(self.tmp.glob('pack-*.json')))}.json"
        if raw_text is not None:
            pack_path.write_text(raw_text)
        else:
            pack_path.write_text(json.dumps(pack_obj))
        st = eng.empty_state({"dnc_path": str(self.dnc),
                              "rule_pack_path": str(pack_path)})
        return st, pack_path

    def good_setup(self, vendor=False):
        """Mandate + lead minted under the GOOD pack; returns state,
        mandate_id, intake result. Callers then swap the pack path to
        a malformed pack and re-run the paths (Verity's shape: the
        defect fired on revocation/send against existing records)."""
        st, _ = self.state_with_pack(GOOD_PACK)
        m = eng.mandate_create(st, {"brokerage_id": "brokerage-1",
            "officer": "officer-1", "officer_sig": officer_sig(),
            "seats": [SELLER],
            "broker_of_record": "Broker Of Record (synthetic)"})
        mid = m["mandate_id"]
        if vendor:
            r = eng.lead_intake(st, {
                "mandate_id": mid,
                "identity": {"name": "Synthetic Lead",
                             "phones": ["555-0120"],
                             "emails": ["lead@example.invalid"]},
                "source": {"kind": "PORTAL_PROGRAM",
                           "vendor_name": "Synthetic Portal Co",
                           "distribution": "UNKNOWN",
                           "program_channel": "text"},
                "capture": {},
                "channel_grants": {"text": {"basis": "EXPRESS"}},
                "named_seller_set": [SELLER], "at_time": T0})
            self.assertEqual(r["provenance_class"], "VENDOR_ASSERTED")
        else:
            r = eng.lead_intake(st, {
                "mandate_id": mid,
                "identity": {"name": "Synthetic Lead",
                             "phones": ["555-0120"],
                             "emails": ["lead@example.invalid"]},
                "source": {"kind": "OWN_FORM"},
                "capture": {"surface": "web_form",
                            "disclosure_text_verbatim":
                                "Synthetic disclosure",
                            "disclosure_version": "synthetic-v1",
                            "form_snapshot_ref": "snap-1"},
                "channel_grants": {"text": {"basis": "PEWC"},
                                   "voice": {"basis": "PEWC"},
                                   "email": {"basis": "EXPRESS"}},
                "named_seller_set": [SELLER], "at_time": T0})
        return st, mid, r

    def send_text(self, st, lead, mid):
        return eng.call_tool(st, "outreach_text", {
            "lead_id": lead, "mandate_id": mid,
            "sending_identity": SELLER, "at_time": T0,
            "body": "Hello, following up on your inquiry."})


class TestD8MinimalPack(D8Base):
    def test_d8_minimal_pack_refuses_at_load_by_name(self):
        # Byte-for-byte minimal pack: load itself refuses by name.
        st, _ = self.state_with_pack(MINIMAL_PACK)
        got, _ = expect_refusal(lambda: eng.load_rule_pack(st))
        self.assertEqual(got, "RULE_PACK_UNAVAILABLE")
        # Mandate creation (the probe's posture=None source) refuses:
        # no mandate can be minted, so vendor_lead_posture is never
        # None under a malformed pack.
        got, _ = expect_refusal(lambda: eng.mandate_create(st, {
            "brokerage_id": "brokerage-1", "officer": "officer-1",
            "officer_sig": officer_sig(), "seats": [SELLER]}))
        self.assertEqual(got, "RULE_PACK_UNAVAILABLE")
        self.assertEqual(st["mandates"], {})

    def test_d8_minimal_pack_intake_send_revocation_all_refuse(self):
        # Existing record, pack swapped to the byte-for-byte minimal
        # pack: intake mints nothing, a send does NOT send, and
        # revocation_record refuses BY NAME (never RAW KeyError).
        st, mid, r = self.good_setup()
        bad, _ = self.state_with_pack(MINIMAL_PACK)
        st["_config"]["rule_pack_path"] = bad["_config"]["rule_pack_path"]
        got, _ = expect_refusal(lambda: eng.lead_intake(st, {
            "mandate_id": mid,
            "identity": {"name": "S2", "phones": ["555-0199"],
                         "emails": ["s2@example.invalid"]},
            "source": {"kind": "OWN_FORM"}, "capture": {},
            "channel_grants": {}, "named_seller_set": [SELLER],
            "at_time": T0}))
        self.assertEqual(got, "RULE_PACK_UNAVAILABLE")
        self.assertEqual(len(st["leads"]), 1)  # nothing minted
        got, _ = expect_refusal(lambda: self.send_text(
            st, r["lead_id"], mid))
        self.assertEqual(got, "RULE_PACK_UNAVAILABLE")
        got, _ = expect_refusal(lambda: eng.revocation_record(st, {
            "lead_id": r["lead_id"], "received_via_channel": "text",
            "raw_payload": "STOP", "at_time": T0}))
        self.assertEqual(got, "RULE_PACK_UNAVAILABLE")
        self.assertFalse(got.startswith("RAW:"))


class TestD8RevocationVariants(D8Base):
    def test_d8_revocation_empty_object_and_null(self):
        # Byte-for-byte variants: revocation={} and revocation=null
        # on an otherwise-good pack. Pre-fix: RAW KeyError
        # 'honor_window_business_days' / RAW AttributeError.
        for variant in ({}, None):
            pack = copy.deepcopy(GOOD_PACK)
            pack["revocation"] = variant
            st, mid, r = self.good_setup()
            bad, _ = self.state_with_pack(pack)
            st["_config"]["rule_pack_path"] = \
                bad["_config"]["rule_pack_path"]
            got, _ = expect_refusal(lambda: eng.revocation_record(st, {
                "lead_id": r["lead_id"],
                "received_via_channel": "text",
                "raw_payload": "STOP", "at_time": T0}))
            self.assertEqual(got, "RULE_PACK_UNAVAILABLE",
                             f"revocation={variant!r}")
            self.assertFalse(got.startswith("RAW:"))
            got, _ = expect_refusal(lambda: self.send_text(
                st, r["lead_id"], mid))
            self.assertEqual(got, "RULE_PACK_UNAVAILABLE")

    def test_d8_revocation_never_lost_to_pack_shape(self):
        # Revocation against the minimal pack refuses by name and
        # mutates NOTHING; restoring the good pack, the same
        # revocation records cleanly — it was refused, not lost.
        st, mid, r = self.good_setup()
        good_path = st["_config"]["rule_pack_path"]
        bad, _ = self.state_with_pack(MINIMAL_PACK)
        st["_config"]["rule_pack_path"] = bad["_config"]["rule_pack_path"]
        got, _ = expect_refusal(lambda: eng.revocation_record(st, {
            "lead_id": r["lead_id"], "received_via_channel": "text",
            "raw_payload": "STOP", "at_time": T0}))
        self.assertEqual(got, "RULE_PACK_UNAVAILABLE")
        rec = eng.consent_record_read(st, {"record_id": r["record_id"]})
        self.assertEqual(rec["revocations"], [])
        self.assertEqual(rec["revocation_state"], "NONE")
        st["_config"]["rule_pack_path"] = good_path
        out = eng.revocation_record(st, {
            "lead_id": r["lead_id"], "received_via_channel": "text",
            "raw_payload": "STOP", "at_time": T0})
        self.assertIn("revocation_id", out)
        rec = eng.consent_record_read(st, {"record_id": r["record_id"]})
        self.assertEqual(len(rec["revocations"]), 1)
        got, _ = expect_refusal(lambda: self.send_text(
            st, r["lead_id"], mid))
        self.assertEqual(got, "CONSENT_REVOKED")


class TestD8FailOpenPosture(D8Base):
    def test_d8_keyless_pack_no_unlimited_vendor_sends(self):
        # Byte-for-byte fail-open probe: under the keyless pack the
        # pre-fix engine returned vendor_lead_posture=None and a
        # VENDOR_ASSERTED lead sent 3/3 unlimited (good pack refuses
        # send #2 with VENDOR_POSTURE_EXHAUSTED).
        st, _ = self.state_with_pack(MINIMAL_PACK)
        got, _ = expect_refusal(lambda: eng.mandate_create(st, {
            "brokerage_id": "brokerage-1", "officer": "officer-1",
            "officer_sig": officer_sig(), "seats": [SELLER]}))
        self.assertEqual(got, "RULE_PACK_UNAVAILABLE")
        # Swap variant: vendor lead minted under the good pack, pack
        # swapped to keyless — 3 send attempts, 0 sends, all named.
        st2, mid2, r2 = self.good_setup(vendor=True)
        bad, _ = self.state_with_pack(MINIMAL_PACK)
        st2["_config"]["rule_pack_path"] = \
            bad["_config"]["rule_pack_path"]
        for _ in range(3):
            got, _ = expect_refusal(lambda: self.send_text(
                st2, r2["lead_id"], mid2))
            self.assertEqual(got, "RULE_PACK_UNAVAILABLE")
        sends = [e for p in st2["packs"].values() for e in p["entries"]
                 if e["kind"] == "SEND"]
        self.assertEqual(sends, [])


class TestD8SchemaAndD5Family(D8Base):
    def test_d8_mistyped_sections_and_keys_refuse_at_load(self):
        variants = {}
        p = copy.deepcopy(GOOD_PACK); del p["revocation"]
        variants["no revocation section"] = p
        p = copy.deepcopy(GOOD_PACK)
        p["revocation"]["honor_window_business_days"] = "10"
        variants["honor_window string"] = p
        p = copy.deepcopy(GOOD_PACK)
        p["revocation"]["clarification"] = None
        variants["clarification null"] = p
        p = copy.deepcopy(GOOD_PACK)
        p["revocation"]["clarification"]["template"] = "Hi {unknown}"
        variants["clarification template unformattable"] = p
        p = copy.deepcopy(GOOD_PACK); del p["vendor_lead_posture_default"]
        variants["no vendor posture default"] = p
        p = copy.deepcopy(GOOD_PACK); p["vendor_lead_posture_default"] = None
        variants["vendor posture null"] = p
        p = copy.deepcopy(GOOD_PACK); del p["dnc"]
        variants["no dnc section"] = p
        p = copy.deepcopy(GOOD_PACK)
        del p["fair_housing_vocabulary_version"]
        variants["no vocab ref"] = p
        p = copy.deepcopy(GOOD_PACK); p["revocation"]["keyword_set"] = "STOP"
        variants["keyword_set string"] = p
        p = copy.deepcopy(GOOD_PACK); del p["set_id"]
        variants["no set_id"] = p
        for name, pack in variants.items():
            st, _ = self.state_with_pack(pack)
            got, _ = expect_refusal(lambda: eng.load_rule_pack(st))
            self.assertEqual(got, "RULE_PACK_UNAVAILABLE", name)
            self.assertFalse(got.startswith("RAW:"), name)

    def test_d8_d5_family_uniformly_closed(self):
        # D5 shapes re-run (scoped re-bench family): invalid JSON,
        # unreadable (directory as pack path), non-object packs.
        st, mid, r = self.good_setup()
        cases = {}
        p1 = self.tmp / "invalid.json"; p1.write_text("{not json")
        cases["invalid_json"] = str(p1)
        cases["unreadable_dir"] = str(self.tmp)
        for label, text in (("array", '["x"]'), ("string", '"x"'),
                            ("number", "42")):
            pp = self.tmp / f"nonobj-{label}.json"; pp.write_text(text)
            cases[f"nonobject_{label}"] = str(pp)
        for name, path in cases.items():
            st["_config"]["rule_pack_path"] = path
            got, _ = expect_refusal(lambda: self.send_text(
                st, r["lead_id"], mid))
            self.assertEqual(got, "RULE_PACK_UNAVAILABLE", name)
            got, _ = expect_refusal(lambda: eng.revocation_record(st, {
                "lead_id": r["lead_id"],
                "received_via_channel": "text",
                "raw_payload": "STOP", "at_time": T0}))
            self.assertEqual(got, "RULE_PACK_UNAVAILABLE", name)

    def test_d8_by_ref_malformed_archive_pack_refuses_by_name(self):
        # Verifier path: a matched-but-malformed archive pack must
        # refuse RULE_PACK_UNAVAILABLE, never reach the pinned
        # revocation arithmetic as a raw KeyError.
        st, mid, r = self.good_setup()
        eng.revocation_record(st, {"lead_id": r["lead_id"],
            "received_via_channel": "text", "raw_payload": "STOP",
            "at_time": T0})
        pack = list(st["packs"].values())[0]
        packdir = self.tmp / "packs"; packdir.mkdir()
        malformed = {"set_id": "SYNTHETIC_DEFAULT", "version": "v1",
                     "set_by": "SYNTHETIC_DEFAULT"}
        (packdir / "SYNTHETIC_DEFAULT-v1.json").write_text(
            json.dumps(malformed))
        st["_config"]["rule_pack_dir"] = str(packdir)
        ref = pack["revocations"][0]["scope_rule_version"]
        got, _ = expect_refusal(
            lambda: eng.load_rule_pack_by_ref(st, ref))
        self.assertEqual(got, "RULE_PACK_UNAVAILABLE")
        got, _ = expect_refusal(lambda: eng.consent_record_verify(
            st, {"mode": "PACK", "pack_id": pack["pack_id"]}))
        self.assertEqual(got, "RULE_PACK_UNAVAILABLE")
        self.assertFalse(got.startswith("RAW:"))


if __name__ == "__main__":
    unittest.main()

# Eclipse Covenant MCP · © 2026 Cyan Eclipse Technologies LLC · cetech.ai
