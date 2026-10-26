# Eclipse Covenant MCP
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
# Fix-round regressions D8b: Verity scoped re-bench -3 shapes
# replicated BYTE-FOR-BYTE from the retained scorecard
# (eclipse-covenant-mcp-2026-10-25-3.json, PARTIAL 27/29, D8b HIGH):
# vendor posture is a closed vocabulary validated as a free string.

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

# Verity D8b surviving probes, byte-for-byte:
TYPO_POSTURE = "NARROW_FIRST_CONTACT_ONLI"      # one-char typo
LOWER_POSTURE = "narrow_first_contact_only"     # lowercase variant
UNLIMITED_POSTURE = "UNLIMITED"                 # no such posture exists
INT_POSTURE = 123                               # mandate override, int


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


class D8bBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dnc = self.tmp / "dnc.json"
        shutil.copy(HERE / "data" / "dnc_fixture.json", self.dnc)

    def state_with_pack(self, pack_obj):
        pack_path = self.tmp / \
            f"pack-{len(list(self.tmp.glob('pack-*.json')))}.json"
        pack_path.write_text(json.dumps(pack_obj))
        st = eng.empty_state({"dnc_path": str(self.dnc),
                              "rule_pack_path": str(pack_path)})
        return st, pack_path

    def mandate(self, st, **extra):
        args = {"brokerage_id": "brokerage-1", "officer": "officer-1",
                "officer_sig": officer_sig(), "seats": [SELLER],
                "broker_of_record": "Broker Of Record (synthetic)"}
        args.update(extra)
        return eng.mandate_create(st, args)

    def vendor_setup(self, pack_obj=GOOD_PACK, **mandate_extra):
        st, _ = self.state_with_pack(pack_obj)
        mid = self.mandate(st, **mandate_extra)["mandate_id"]
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
        return st, mid, r

    def send_text(self, st, lead, mid):
        return eng.call_tool(st, "outreach_text", {
            "lead_id": lead, "mandate_id": mid,
            "sending_identity": SELLER, "at_time": T0,
            "body": "Hello, following up on your inquiry."})

    def send_entries(self, st):
        return [e for p in st["packs"].values() for e in p["entries"]
                if e["kind"] == "SEND"]


class TestD8bPackPostureVocabulary(D8bBase):
    def test_d8b_pack_typo_posture_refuses_at_load(self):
        # Byte-for-byte: vendor_lead_posture_default=
        # "NARROW_FIRST_CONTACT_ONLI" loaded fine pre-fix and vendor
        # sends went 3/3 SENT_SIMULATOR.
        pack = copy.deepcopy(GOOD_PACK)
        pack["vendor_lead_posture_default"] = TYPO_POSTURE
        st, _ = self.state_with_pack(pack)
        got, _ = expect_refusal(lambda: eng.load_rule_pack(st))
        self.assertEqual(got, "RULE_PACK_UNAVAILABLE")
        got, _ = expect_refusal(lambda: self.mandate(st))
        self.assertEqual(got, "RULE_PACK_UNAVAILABLE")
        self.assertEqual(st["mandates"], {})
        # Swap variant: vendor lead minted under the good pack, pack
        # swapped to the typo pack — 3 attempts, 0 sends, all named.
        st2, mid2, r2 = self.vendor_setup()
        bad, _ = self.state_with_pack(pack)
        st2["_config"]["rule_pack_path"] = \
            bad["_config"]["rule_pack_path"]
        for _ in range(3):
            got, _ = expect_refusal(lambda: self.send_text(
                st2, r2["lead_id"], mid2))
            self.assertEqual(got, "RULE_PACK_UNAVAILABLE")
        self.assertEqual(self.send_entries(st2), [])

    def test_d8b_pack_lowercase_and_unlimited_refuse_at_load(self):
        # Byte-for-byte followup probes: lowercase
        # "narrow_first_contact_only" and invented "UNLIMITED" both
        # loaded pre-fix; vendor sends went 3/3 SENT_SIMULATOR.
        for bad_value in (LOWER_POSTURE, UNLIMITED_POSTURE):
            pack = copy.deepcopy(GOOD_PACK)
            pack["vendor_lead_posture_default"] = bad_value
            st, _ = self.state_with_pack(pack)
            got, _ = expect_refusal(lambda: eng.load_rule_pack(st))
            self.assertEqual(got, "RULE_PACK_UNAVAILABLE", bad_value)
            st2, mid2, r2 = self.vendor_setup()
            bad, _ = self.state_with_pack(pack)
            st2["_config"]["rule_pack_path"] = \
                bad["_config"]["rule_pack_path"]
            for _ in range(3):
                got, _ = expect_refusal(lambda: self.send_text(
                    st2, r2["lead_id"], mid2))
                self.assertEqual(got, "RULE_PACK_UNAVAILABLE",
                                 bad_value)
            self.assertEqual(self.send_entries(st2), [], bad_value)


class TestD8bMandateOverride(D8bBase):
    def test_d8b_mandate_override_typo_refused_never_stored(self):
        # Byte-for-byte: GOOD pack + mandate_create
        # vendor_lead_posture="NARROW_FIRST_CONTACT_ONLI" was
        # accepted verbatim pre-fix; vendor sends went 3/3.
        st, _ = self.state_with_pack(GOOD_PACK)
        got, _ = expect_refusal(
            lambda: self.mandate(st, vendor_lead_posture=TYPO_POSTURE))
        self.assertEqual(got, "INVALID_VENDOR_POSTURE")
        self.assertFalse(got.startswith("RAW:"))
        self.assertEqual(st["mandates"], {})  # never stored verbatim

    def test_d8b_mandate_override_int_refused_never_stored(self):
        # Byte-for-byte: GOOD pack + mandate_create
        # vendor_lead_posture=123 (int) was accepted verbatim
        # (posture=123) pre-fix; vendor sends went 3/3.
        st, _ = self.state_with_pack(GOOD_PACK)
        got, _ = expect_refusal(
            lambda: self.mandate(st, vendor_lead_posture=INT_POSTURE))
        self.assertEqual(got, "INVALID_VENDOR_POSTURE")
        self.assertFalse(got.startswith("RAW:"))
        self.assertEqual(st["mandates"], {})

    def test_d8b_mandate_override_lowercase_and_unlimited_refused(self):
        for bad_value in (LOWER_POSTURE, UNLIMITED_POSTURE):
            st, _ = self.state_with_pack(GOOD_PACK)
            got, _ = expect_refusal(lambda: self.mandate(
                st, vendor_lead_posture=bad_value))
            self.assertEqual(got, "INVALID_VENDOR_POSTURE", bad_value)
            self.assertEqual(st["mandates"], {}, bad_value)


class TestD8bGateFailClosedElse(D8bBase):
    def test_d8b_gate_unknown_posture_refuses_by_name_no_send(self):
        # Defense in depth: a bad posture value reaching the Gate
        # directly (mandate state written bypassing mandate_create,
        # e.g. restored state) hits the fail-closed else — named
        # refusal on EVERY attempt, 0 sends, for each D8b shape.
        for bad_value in (TYPO_POSTURE, LOWER_POSTURE,
                          UNLIMITED_POSTURE, INT_POSTURE):
            st, mid, r = self.vendor_setup()
            st["mandates"][mid]["vendor_lead_posture"] = bad_value
            for _ in range(3):
                got, _ = expect_refusal(lambda: self.send_text(
                    st, r["lead_id"], mid))
                self.assertEqual(got, "VENDOR_POSTURE_UNKNOWN",
                                 repr(bad_value))
                self.assertFalse(got.startswith("RAW:"))
            self.assertEqual(self.send_entries(st), [], repr(bad_value))


class TestD8bRigidityAndB5(D8bBase):
    def test_d8b_rigidity_revalued_refuse_all_pack_loads_and_governs(
            self):
        # Rigidity counter-probe: a DIFFERENT legitimate vocabulary
        # value is rule data, not a defect — the re-valued pack
        # loads (NO_REFUSAL) and governs: vendor send refuses under
        # REFUSE_ALL semantics, exactly as the bench-3 RIG line ran.
        pack = copy.deepcopy(GOOD_PACK)
        pack["vendor_lead_posture_default"] = "REFUSE_ALL"
        st, mid, r = self.vendor_setup(pack_obj=pack)
        got, _ = expect_refusal(lambda: eng.load_rule_pack(st))
        self.assertEqual(got, "NO_REFUSAL")
        got, _ = expect_refusal(lambda: self.send_text(
            st, r["lead_id"], mid))
        self.assertEqual(got, "CONSENT_RECORD_MISSING")
        self.assertEqual(self.send_entries(st), [])

    def test_d8b_b5_good_pack_behavior_unchanged(self):
        # B5 regression line: good pack, VENDOR_ASSERTED lead —
        # send 1 SENT_SIMULATOR, send 2 VENDOR_POSTURE_EXHAUSTED.
        st, mid, r = self.vendor_setup()
        out = self.send_text(st, r["lead_id"], mid)
        self.assertEqual(out["status"], "SENT_SIMULATOR")
        self.assertEqual(out["provenance_class"], "VENDOR_ASSERTED")
        got, _ = expect_refusal(lambda: self.send_text(
            st, r["lead_id"], mid))
        self.assertEqual(got, "VENDOR_POSTURE_EXHAUSTED")
        # A legitimate explicit override still mints and governs:
        # NARROW override behaves identically; REFUSE_ALL override
        # refuses the vendor send by name.
        st2, mid2, r2 = self.vendor_setup(
            vendor_lead_posture="NARROW_FIRST_CONTACT_ONLY")
        out = self.send_text(st2, r2["lead_id"], mid2)
        self.assertEqual(out["status"], "SENT_SIMULATOR")
        st3, mid3, r3 = self.vendor_setup(
            vendor_lead_posture="REFUSE_ALL")
        got, _ = expect_refusal(lambda: self.send_text(
            st3, r3["lead_id"], mid3))
        self.assertEqual(got, "CONSENT_RECORD_MISSING")
        self.assertEqual(self.send_entries(st3), [])


if __name__ == "__main__":
    unittest.main()

# Eclipse Covenant MCP · © 2026 Cyan Eclipse Technologies LLC · cetech.ai
