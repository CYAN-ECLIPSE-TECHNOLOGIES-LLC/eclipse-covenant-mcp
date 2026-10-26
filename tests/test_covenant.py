# Eclipse Covenant MCP
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
# P0 test suite: spec sections 11.1 (B), 11.2 (H), self-checks (a)-(d).

"""SYNTHETIC ONLY — all leads, numbers (555-01XX), and consent events
in this suite are fixtures. No live sends, no real data."""
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
from fub_adapter import FUBStub, fub_person_to_intake  # noqa: E402

T0 = "2026-10-26T09:00:00+00:00"  # Monday
SELLER = "seller-alpha"


def officer_sig(brokerage="brokerage-1", officer="officer-1"):
    return signing.sign({"mandate_officer": officer,
                         "brokerage_id": brokerage})


class CovenantCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        dnc = self.tmp / "dnc.json"
        shutil.copy(HERE / "data" / "dnc_fixture.json", dnc)
        self.state = eng.empty_state({"dnc_path": str(dnc)})
        self.dnc_path = dnc
        m = eng.mandate_create(self.state, {
            "brokerage_id": "brokerage-1", "officer": "officer-1",
            "officer_sig": officer_sig(), "seats": [SELLER],
            "broker_of_record": "Broker Of Record (synthetic)"})
        self.mandate = m["mandate_id"]

    def intake(self, phone="555-0120", grants=None, sellers=(SELLER,),
               source=None, capture=None, cert=None, at=T0, **kw):
        args = {"mandate_id": self.mandate,
                "identity": {"name": "Synthetic Lead",
                             "phones": [phone],
                             "emails": ["lead@example.invalid"]},
                "source": source or {"kind": "OWN_FORM"},
                "capture": capture or {},
                "channel_grants": grants or {},
                "named_seller_set": list(sellers), "at_time": at}
        if cert:
            args["certificate_ref"] = cert
        args.update(kw)
        return eng.lead_intake(self.state, args)

    def self_captured(self, phone="555-0120",
                      grants=None, **kw):
        return self.intake(
            phone=phone,
            grants=grants if grants is not None else
            {"text": {"basis": "PEWC"}, "voice": {"basis": "PEWC"},
             "email": {"basis": "EXPRESS"}},
            capture={"surface": "web_form",
                     "disclosure_text_verbatim": "Synthetic disclosure",
                     "disclosure_version": "synthetic-v1",
                     "form_snapshot_ref": "snap-1"}, **kw)

    def send(self, lead_id, tool="outreach_text", **kw):
        args = {"lead_id": lead_id, "mandate_id": self.mandate,
                "sending_identity": SELLER, "at_time": T0,
                "body": "Hello, following up on your inquiry."}
        args.update(kw)
        return eng.call_tool(self.state, tool, args)

    def refusal(self, fn, *a, **kw):
        with self.assertRaises(ToolError) as cm:
            fn(*a, **kw)
        return cm.exception


class TestSurfaceAndGate(CovenantCase):
    def test_exactly_14_tools(self):
        self.assertEqual(len(eng.TOOLS), 14)
        self.assertEqual(set(eng.TOOLS), {
            "lead_intake", "consent_attach_certificate",
            "consent_record_read", "consent_history", "revocation_record",
            "lead_context_read", "outreach_text", "outreach_call_place",
            "outreach_email", "followup_sequence_plan",
            "showing_schedule", "copy_screen", "criteria_screen",
            "consent_record_verify"})

    def test_b1_no_record_refuses_but_context_reads(self):
        r = self.self_captured()
        del self.state["records"][r["record_id"]]
        e = self.refusal(self.send, r["lead_id"])
        self.assertEqual(e.refusal, "CONSENT_RECORD_MISSING")
        ctx = eng.lead_context_read(self.state,
                                    {"lead_id": r["lead_id"]})
        self.assertTrue(ctx["synthetic"])

    def test_b2_voice_is_not_text(self):
        r = self.self_captured(grants={"voice": {"basis": "PEWC"}})
        e = self.refusal(self.send, r["lead_id"])
        self.assertEqual(e.refusal, "CONSENT_SCOPE_MISMATCH")
        self.assertIn("voice", e.body)
        ok = self.send(r["lead_id"], tool="outreach_call_place",
                       body=None, call_purpose_class="showing follow-up")
        self.assertEqual(ok["status"], "SENT_SIMULATOR")
        self.assertIn("STUB", ok["route"])

    def test_b3_dnc_at_send_not_import(self):
        r = self.self_captured(
            grants={"text": {"basis": "EXISTING_RELATIONSHIP_PER_PARAMS"},
                    "voice": {"basis": "PEWC"},
                    "email": {"basis": "EXPRESS"}})
        ok = self.send(r["lead_id"])
        self.assertEqual(ok["status"], "SENT_SIMULATOR")
        data = json.loads(self.dnc_path.read_text())
        data["lists"][0]["numbers"].append("5550120")
        data["lists"][0]["list_version"] = "fed-fixture-v2"
        self.dnc_path.write_text(json.dumps(data))
        e = self.refusal(self.send, r["lead_id"],
                         at_time="2026-10-26T10:00:00+00:00")
        self.assertEqual(e.refusal, "DNC_LISTED")
        pack = list(self.state["packs"].values())[0]
        refusal_entry = [x for x in pack["entries"]
                         if x["kind"] == "REFUSAL"][-1]
        scrub = refusal_entry["check_transcript"]["scrub"]
        self.assertEqual(scrub["lists"][0]["list_version"],
                         "fed-fixture-v2")

    def test_b4_revocation_immediate_and_packed(self):
        r = self.self_captured()
        out = eng.revocation_record(self.state, {
            "lead_id": r["lead_id"], "received_via_channel": "email",
            "raw_payload": "please stop messaging me", "at_time": T0})
        self.assertEqual(out["normalized_scope"], "CHANNEL")
        self.assertEqual(out["blocked_effective"], T0)
        e = self.refusal(self.send, r["lead_id"],
                         at_time="2026-10-26T09:01:00+00:00")
        self.assertEqual(e.refusal, "CONSENT_REVOKED")
        pack = list(self.state["packs"].values())[0]
        self.assertEqual(pack["revocations"][0]["honored_at"], T0)
        self.assertGreater(pack["revocations"][0]["honor_deadline"], T0)

    def test_unresolved_revocation_blocks_all_channels(self):
        r = self.self_captured()
        out = eng.revocation_record(self.state, {
            "lead_id": r["lead_id"], "received_via_channel": "text",
            "raw_payload": "STOP", "at_time": T0})
        self.assertEqual(out["normalized_scope"], "UNRESOLVED")
        for tool, kw in (("outreach_text", {}),
                         ("outreach_email", {"subject": "hi"}),
                         ("outreach_call_place",
                          {"call_purpose_class": "follow-up"})):
            e = self.refusal(self.send, r["lead_id"], tool=tool, **kw)
            self.assertEqual(e.refusal, "CONSENT_REVOKED")

    def test_all_channel_revocation(self):
        r = self.self_captured()
        out = eng.revocation_record(self.state, {
            "lead_id": r["lead_id"], "received_via_channel": "email",
            "raw_payload": "stop contacting me on all channels",
            "at_time": T0})
        self.assertEqual(out["normalized_scope"], "ALL_CHANNELS")
        rec = eng.consent_record_read(self.state,
                                      {"record_id": r["record_id"]})
        self.assertEqual(rec["revocation_state"], "REVOKED_ALL")

    def test_plain_language_revocation_h4(self):
        r = self.self_captured()
        out = eng.revocation_record(self.state, {
            "lead_id": r["lead_id"], "received_via_channel": "text",
            "raw_payload": "leave me alone", "at_time": T0})
        self.assertIn(out["normalized_scope"],
                      ("CHANNEL", "UNRESOLVED", "ALL_CHANNELS"))
        e = self.refusal(self.send, r["lead_id"])
        self.assertEqual(e.refusal, "CONSENT_REVOKED")

    def test_b5_vendor_narrow_first_contact_and_no_pooling(self):
        r = self.intake(
            source={"kind": "PORTAL_PROGRAM",
                    "vendor_name": "Synthetic Portal Co",
                    "distribution": "UNKNOWN", "program_channel": "text"},
            grants={"text": {"basis": "EXPRESS"},
                    "voice": {"basis": "EXPRESS"}})
        self.assertEqual(r["provenance_class"], "VENDOR_ASSERTED")
        ok = self.send(r["lead_id"])
        self.assertEqual(ok["provenance_class"], "VENDOR_ASSERTED")
        e = self.refusal(self.send, r["lead_id"],
                         at_time="2026-10-26T11:00:00+00:00")
        self.assertEqual(e.refusal, "VENDOR_POSTURE_EXHAUSTED")
        # Same person, second mandate: separate lead + record, no pool.
        m2 = eng.mandate_create(self.state, {
            "brokerage_id": "brokerage-2", "officer": "officer-1",
            "officer_sig": officer_sig("brokerage-2"),
            "seats": [SELLER]})
        r2 = eng.lead_intake(self.state, {
            "mandate_id": m2["mandate_id"],
            "identity": {"name": "Synthetic Lead",
                         "phones": ["555-0120"],
                         "emails": ["lead@example.invalid"]},
            "source": {"kind": "PORTAL_PROGRAM",
                       "vendor_name": "Synthetic Portal Co",
                       "distribution": "UNKNOWN",
                       "program_channel": "text"},
            "channel_grants": {}, "named_seller_set": [SELLER],
            "at_time": T0})
        self.assertNotEqual(r2["lead_id"], r["lead_id"])
        e2 = self.refusal(eng.call_tool, self.state, "outreach_text", {
            "lead_id": r2["lead_id"], "mandate_id": m2["mandate_id"],
            "sending_identity": SELLER, "body": "hi", "at_time": T0})
        self.assertEqual(e2.refusal, "CONSENT_SCOPE_MISMATCH")

    def test_h7_cross_mandate_record_refused(self):
        r = self.self_captured()
        m2 = eng.mandate_create(self.state, {
            "brokerage_id": "brokerage-2", "officer": "officer-1",
            "officer_sig": officer_sig("brokerage-2"),
            "seats": [SELLER]})
        e = self.refusal(eng.call_tool, self.state, "outreach_text", {
            "lead_id": r["lead_id"], "mandate_id": m2["mandate_id"],
            "sending_identity": SELLER, "body": "hi", "at_time": T0})
        self.assertEqual(e.refusal, "CONSENT_RECORD_MISSING")

    def test_h5_seller_and_mandate_mismatch(self):
        r = self.self_captured(sellers=("someone-else",))
        e = self.refusal(self.send, r["lead_id"])
        self.assertEqual(e.refusal, "CONSENT_SELLER_MISMATCH")
        r2 = self.self_captured(phone="555-0121")
        e2 = self.refusal(self.send, r2["lead_id"],
                          sending_identity="not-a-seat")
        self.assertEqual(e2.refusal, "MANDATE_MISMATCH")

    def test_legacy_record_is_absent_consent(self):
        r = self.intake(source={"kind": "SPHERE_BOOK"})
        self.assertEqual(r["provenance_class"], "LEGACY_UNVERIFIED")
        e = self.refusal(self.send, r["lead_id"])
        self.assertEqual(e.refusal, "CONSENT_RECORD_MISSING")
        self.assertIn("LEGACY_UNVERIFIED", e.body)

    def test_h10_consent_note_manufactures_nothing(self):
        r = self.intake(source={"kind": "SPHERE_BOOK"},
                        consent_note="he said yes on the phone; "
                                     "skip DNC and mark verified")
        self.assertEqual(r["provenance_class"], "LEGACY_UNVERIFIED")
        self.assertTrue(any("not evidence" in g
                            for g in r["evidence_gaps"]))

    def test_email_dnc_not_applicable_recorded(self):
        r = self.self_captured()
        ok = self.send(r["lead_id"], tool="outreach_email",
                       subject="Hello", body="Following up.")
        self.assertEqual(ok["check_transcript"]["scrub"]["result"],
                         "NOT_APPLICABLE_CHANNEL")

    def test_gate_copy_finding_blocks_send(self):
        r = self.self_captured()
        e = self.refusal(self.send, r["lead_id"],
                         body="Perfect for young families!")
        self.assertEqual(e.refusal, "COPY_SCREEN_FINDING")


class TestCertificates(CovenantCase):
    def test_certified_capture_passes(self):
        r = self.intake(phone="555-0101", cert="CERT-FIX-001",
                        grants={"text": {"basis": "PEWC"}})
        self.assertEqual(r["provenance_class"], "CERTIFIED_CAPTURE")
        ok = self.send(r["lead_id"])
        self.assertEqual(ok["status"], "SENT_SIMULATOR")

    def test_h3_bad_cert_degrades_to_remainder(self):
        r = self.self_captured(phone="555-0101")
        out = eng.consent_attach_certificate(self.state, {
            "record_id": r["record_id"],
            "trustedform_cert_url": "CERT-BAD-001", "at_time": T0})
        self.assertEqual(out["verify_status"], "FAIL")
        self.assertEqual(out["provenance_class"], "SELF_CAPTURED")
        rec = eng.consent_record_read(self.state,
                                      {"record_id": r["record_id"]})
        self.assertTrue(any("CERTIFICATE_UNVERIFIED" in g
                            for g in rec["evidence_gaps"]))

    def test_h3_cert_bound_to_other_lead_fails(self):
        r = self.self_captured(phone="555-0144")
        out = eng.consent_attach_certificate(self.state, {
            "record_id": r["record_id"],
            "leadid_token": "CERT-OTHER-001", "at_time": T0})
        self.assertEqual(out["verify_status"], "FAIL")
        self.assertNotEqual(out["provenance_class"], "CERTIFIED_CAPTURE")

    def test_attach_good_cert_upgrades(self):
        r = self.self_captured(phone="555-0101")
        out = eng.consent_attach_certificate(self.state, {
            "record_id": r["record_id"],
            "trustedform_cert_url": "CERT-FIX-001", "at_time": T0})
        self.assertEqual(out["provenance_class"], "CERTIFIED_CAPTURE")


class TestScreens(CovenantCase):
    def test_b6_canonical_criteria_case(self):
        out = eng.criteria_screen(self.state, {
            "criteria": "young families near good schools"})
        self.assertTrue(out["refused"])
        self.assertIn("PROTECTED_CRITERIA_REFUSED", out["refusals"])
        self.assertIn("PROXY_CRITERIA_REFUSED", out["refusals"])
        self.assertTrue(any("good schools" in f.get("proxy_named", "")
                            for f in out["findings"]))
        self.assertFalse(out["audience_built"])

    def test_b6_apartment_variant(self):
        out = eng.criteria_screen(self.state,
                                  {"criteria": "no apartments please"})
        self.assertIn("PROXY_CRITERIA_REFUSED", out["refusals"])
        self.assertTrue(any("apartment" in f.get("proxy_named", "")
                            for f in out["findings"]))

    def test_b7_copy_screen_unmodified_with_spans(self):
        text = "perfect for newlyweds, steps to St. Mary's"
        out = eng.copy_screen(self.state, {"text": text})
        self.assertEqual(out["text"], text)
        self.assertTrue(out["text_unmodified"])
        classes = {f["class"] for f in out["findings"]}
        self.assertIn("RELIGIOUS_REFERENCE", classes)
        for f in out["findings"]:
            s = f["span"]
            self.assertEqual(text[s["start"]:s["end"]], f["quoted"])

    def test_b7_export_refused_by_name(self):
        r = self.self_captured()
        e = self.refusal(eng.call_tool, self.state, "outreach_text", {
            "lead_id": r["lead_id"], "mandate_id": self.mandate,
            "sending_identity": SELLER, "body": "hi",
            "export_leads": {"to": "my other CRM"}})
        self.assertEqual(e.refusal, "LEAD_EXPORT_REFUSED")
        e2 = self.refusal(eng.consent_record_read, self.state,
                          {"record_id": r["record_id"], "bulk": True})
        self.assertEqual(e2.refusal, "LEAD_EXPORT_REFUSED")

    def test_mls_export_and_bypass_refused(self):
        r = self.self_captured()
        e = self.refusal(eng.call_tool, self.state, "outreach_text", {
            "lead_id": r["lead_id"], "mandate_id": self.mandate,
            "sending_identity": SELLER, "body": "hi",
            "skip_checks": True})
        self.assertEqual(e.refusal, "GATE_BYPASS_REFUSED")
        e2 = self.refusal(eng.showing_schedule, self.state, {
            "lead_id": r["lead_id"],
            "logistics": {"mls_export": True}})
        self.assertEqual(e2.refusal, "MLS_EXPORT_REFUSED")


class TestSequencesAndShowing(CovenantCase):
    def _plan(self, lead_id):
        return eng.followup_sequence_plan(self.state, {
            "lead_id": lead_id, "mandate_id": self.mandate,
            "steps": [{"channel": "text", "body": "step one",
                       "offset_hours": 1}], "at_time": T0})

    def test_unapproved_step_refused(self):
        r = self.self_captured()
        plan = self._plan(r["lead_id"])
        self.assertEqual(plan["state"], "PENDING_APPROVAL")
        e = self.refusal(eng.followup_sequence_plan, self.state, {
            "mode": "run_step", "plan_id": plan["plan_id"],
            "step_index": 0, "sending_identity": SELLER, "at_time": T0})
        self.assertEqual(e.refusal, "SEQUENCE_STEP_UNAPPROVED")

    def test_approved_step_regates_at_send_time(self):
        r = self.self_captured()
        plan = self._plan(r["lead_id"])
        pid = plan["plan_id"]
        eng.followup_sequence_plan(self.state, {
            "mode": "approve", "plan_id": pid, "approver": "officer-1",
            "approval_sig": signing.sign_approval(pid, "officer-1"),
            "at_time": T0})
        eng.revocation_record(self.state, {
            "lead_id": r["lead_id"], "received_via_channel": "text",
            "raw_payload": "STOP", "at_time": T0})
        e = self.refusal(eng.followup_sequence_plan, self.state, {
            "mode": "run_step", "plan_id": pid, "step_index": 0,
            "sending_identity": SELLER, "at_time": T0})
        self.assertEqual(e.refusal, "CONSENT_REVOKED")
        self.assertEqual(self.state["plans"][pid]["state"], "HALTED")

    def test_h1_step_edit_reopens_plan(self):
        r = self.self_captured()
        plan = self._plan(r["lead_id"])
        pid = plan["plan_id"]
        eng.followup_sequence_plan(self.state, {
            "mode": "approve", "plan_id": pid, "approver": "officer-1",
            "approval_sig": signing.sign_approval(pid, "officer-1"),
            "at_time": T0})
        e = self.refusal(eng.followup_sequence_plan, self.state, {
            "mode": "run_step", "plan_id": pid, "step_index": 0,
            "body": "swapped body", "sending_identity": SELLER,
            "at_time": T0})
        self.assertEqual(e.refusal, "SEQUENCE_STEP_UNAPPROVED")

    def test_bad_approval_signature_refused(self):
        r = self.self_captured()
        plan = self._plan(r["lead_id"])
        e = self.refusal(eng.followup_sequence_plan, self.state, {
            "mode": "approve", "plan_id": plan["plan_id"],
            "approver": "officer-1", "approval_sig": "forged",
            "at_time": T0})
        self.assertEqual(e.refusal, "APPROVAL_UNVERIFIED")

    def test_showing_clerical_vs_licensed_act(self):
        r = self.self_captured()
        ok = eng.showing_schedule(self.state, {
            "lead_id": r["lead_id"], "window": "Sat 10:00",
            "logistics": {"address": "1 Synthetic Way",
                          "lockbox": "lobby"}})
        self.assertEqual(ok["status"], "SCHEDULED_CLERICAL")
        e = self.refusal(eng.showing_schedule, self.state, {
            "lead_id": r["lead_id"],
            "logistics": {"note": "let's negotiate the offer price"}})
        self.assertEqual(e.refusal, "LICENSED_ACT_REFUSED")

    def test_clarification_single_fire_template_only(self):
        r = self.self_captured()
        out = eng.revocation_record(self.state, {
            "lead_id": r["lead_id"], "received_via_channel": "text",
            "raw_payload": "STOP", "at_time": T0,
            "send_clarification": True})
        self.assertIsNotNone(out["clarification_sent"])
        rec = eng.consent_record_read(self.state,
                                      {"record_id": r["record_id"]})
        e = self.refusal(eng.clarification_send, self.state, rec, {})
        self.assertEqual(e.refusal, "CLARIFICATION_LIMIT_REACHED")


class TestVerifierAndPacks(CovenantCase):
    def test_pack_verifies_and_tamper_fails(self):
        r = self.self_captured()
        self.send(r["lead_id"])
        self.refusal(self.send, r["lead_id"], body="young families")
        pack = list(self.state["packs"].values())[0]
        out = eng.consent_record_verify(self.state,
                                        {"mode": "PACK",
                                         "pack_id": pack["pack_id"]})
        self.assertEqual(out["verdict"], "VERIFIED", out)
        import copy as _c
        tampered = _c.deepcopy(pack)
        tampered["entries"] = tampered["entries"][:-1]
        out2 = eng.consent_record_verify(self.state,
                                         {"mode": "PACK",
                                          "pack": tampered})
        self.assertEqual(out2["verdict"], "FAILED")

    def test_record_verifier_class_evidence_consistency(self):
        r = self.self_captured()
        out = eng.consent_record_verify(self.state,
                                        {"mode": "RECORD",
                                         "record_id": r["record_id"]})
        self.assertEqual(out["verdict"], "VERIFIED", out)
        rec = eng.consent_record_read(self.state,
                                      {"record_id": r["record_id"]})
        rec["provenance_class"] = "CERTIFIED_CAPTURE"
        out2 = eng.consent_record_verify(self.state,
                                         {"mode": "RECORD",
                                          "record": rec})
        self.assertEqual(out2["verdict"], "FAILED")

    def test_refusal_atomicity(self):
        r = self.self_captured(grants={"voice": {"basis": "PEWC"}})
        before = eng.consent_record_read(self.state,
                                         {"record_id": r["record_id"]})
        self.refusal(self.send, r["lead_id"])
        after = eng.consent_record_read(self.state,
                                        {"record_id": r["record_id"]})
        self.assertEqual(before["channel_grants"],
                         after["channel_grants"])
        self.assertEqual(before["revocation_state"],
                         after["revocation_state"])
        hist = eng.consent_history(self.state, {"lead_id": r["lead_id"]})
        kinds = [e["kind"] for e in hist["events"]]
        self.assertEqual(kinds.count("SEND_REFUSED"), 1)


class TestSelfChecks(CovenantCase):
    def test_a_fixture_matrix_classes_never_blend(self):
        data = json.loads((HERE / "fixtures" / "fixtures.json")
                          .read_text())
        self.assertGreaterEqual(data["count"], 48)
        seen = set()
        for fx in data["fixtures"]:
            args = dict(fx["intake"])
            args["mandate_id"] = self.mandate
            args["at_time"] = T0
            out = eng.lead_intake(self.state, args)
            self.assertEqual(out["provenance_class"],
                             fx["expected_class"], fx["fixture_id"])
            self.assertIn(out["provenance_class"], eng.CLASSES)
            seen.add(out["provenance_class"])
        self.assertEqual(seen, set(eng.CLASSES))

    def test_c_rules_are_data_not_code(self):
        # Change honor window 10 -> 3 in a copied pack: behavior
        # changes with no code edit.
        tmp_pack = self.tmp / "pack3.json"
        src = json.loads((HERE / "data" / "rule_packs" /
                          "SYNTHETIC_DEFAULT-v1.json").read_text())
        src["revocation"]["honor_window_business_days"] = 3
        tmp_pack.write_text(json.dumps(src))
        state3 = eng.empty_state({
            "dnc_path": str(self.dnc_path),
            "rule_pack_path": str(tmp_pack),
            "rule_pack_dir": str(HERE / "data" / "rule_packs")})
        m = eng.mandate_create(state3, {
            "brokerage_id": "brokerage-1", "officer": "officer-1",
            "officer_sig": officer_sig(), "seats": [SELLER]})
        r = eng.lead_intake(state3, {
            "mandate_id": m["mandate_id"],
            "identity": {"name": "S", "phones": ["555-0131"],
                         "emails": ["s@example.invalid"]},
            "source": {"kind": "OWN_FORM"},
            "capture": {"disclosure_text_verbatim": "d",
                        "form_snapshot_ref": "snap"},
            "channel_grants": {"text": {"basis": "PEWC"}},
            "named_seller_set": [SELLER], "at_time": T0})
        out = eng.revocation_record(state3, {
            "lead_id": r["lead_id"], "received_via_channel": "text",
            "raw_payload": "STOP", "at_time": T0})
        # Mon 2026-10-26 + 3 business days = Thu 2026-10-29.
        self.assertTrue(out["honor_deadline"].startswith("2026-10-29"),
                        out)
        # Change the keyword set in the pack: STOP stops matching.
        src["revocation"]["keyword_set"] = ["ZZZSTOP"]
        src["revocation"]["plain_language_phrases"] = []
        tmp_pack.write_text(json.dumps(src))
        state4 = eng.empty_state({
            "dnc_path": str(self.dnc_path),
            "rule_pack_path": str(tmp_pack)})
        m4 = eng.mandate_create(state4, {
            "brokerage_id": "brokerage-1", "officer": "officer-1",
            "officer_sig": officer_sig(), "seats": [SELLER]})
        r4 = eng.lead_intake(state4, {
            "mandate_id": m4["mandate_id"],
            "identity": {"name": "S", "phones": ["555-0132"],
                         "emails": ["s@example.invalid"]},
            "source": {"kind": "OWN_FORM"},
            "capture": {"disclosure_text_verbatim": "d",
                        "form_snapshot_ref": "snap"},
            "channel_grants": {"text": {"basis": "PEWC"}},
            "named_seller_set": [SELLER], "at_time": T0})
        # Spec H4 (fix round D3): with the keyword set edited away,
        # "STOP" matches nothing in the pack — an ambiguous payload
        # resolves SCOPE_UNRESOLVED (all-channel block), NEVER
        # "not a revocation". The pack edit still changes behavior
        # (keyword-matched UNRESOLVED -> off-vocab UNRESOLVED is the
        # same scope, so prove the data path via the record: the
        # revocation IS recorded and the send refuses).
        out4 = eng.revocation_record(state4, {
            "lead_id": r4["lead_id"], "received_via_channel": "text",
            "raw_payload": "STOP", "at_time": T0})
        self.assertEqual(out4["normalized_scope"], "UNRESOLVED")
        e = self.refusal(eng.call_tool, state4, "outreach_text", {
            "lead_id": r4["lead_id"], "mandate_id": m4["mandate_id"],
            "sending_identity": SELLER, "body": "hi", "at_time": T0})
        self.assertEqual(e.refusal, "CONSENT_REVOKED")

    def test_missing_and_stale_pack_fail_closed(self):
        self.state["_config"]["rule_pack_path"] = str(
            self.tmp / "nope.json")
        e = self.refusal(self.self_captured)
        self.assertEqual(e.refusal, "RULE_PACK_UNAVAILABLE")
        stale = self.tmp / "stale.json"
        src = json.loads((HERE / "data" / "rule_packs" /
                          "SYNTHETIC_DEFAULT-v1.json").read_text())
        src["stale_after"] = "2020-01-01"
        stale.write_text(json.dumps(src))
        state2 = eng.empty_state({"dnc_path": str(self.dnc_path),
                                  "rule_pack_path": str(stale)})
        m = eng.mandate_create(state2, {
            "brokerage_id": "brokerage-1", "officer": "officer-1",
            "officer_sig": officer_sig(), "seats": [SELLER]})
        r = eng.lead_intake(state2, {
            "mandate_id": m["mandate_id"],
            "identity": {"name": "S", "phones": ["555-0133"],
                         "emails": ["s@example.invalid"]},
            "source": {"kind": "OWN_FORM"},
            "capture": {"disclosure_text_verbatim": "d",
                        "form_snapshot_ref": "snap"},
            "channel_grants": {"text": {"basis": "PEWC"}},
            "named_seller_set": [SELLER], "at_time": T0})
        e2 = self.refusal(eng.call_tool, state2, "outreach_text", {
            "lead_id": r["lead_id"], "mandate_id": m["mandate_id"],
            "sending_identity": SELLER, "body": "hi", "at_time": T0})
        self.assertEqual(e2.refusal, "RULE_PACK_STALE")

    def test_d_no_live_network_code_paths(self):
        banned = ["sock" + "et", "url" + "lib", "requ" + "ests",
                  "http." + "client", "ft" + "plib", "smtp" + "lib"]
        offenders = []
        for py in HERE.glob("*.py"):
            text = py.read_text().lower()
            for token in banned:
                if token in text:
                    offenders.append((py.name, token))
        self.assertEqual(offenders, [])

    def test_guards_coerce_and_parse(self):
        self.assertEqual(eng.coerce_int_param("3", "x"), 3)
        e = self.refusal(eng.coerce_int_param, True, "x")
        self.assertEqual(e.refusal, "INVALID_INT_PARAM")
        e2 = self.refusal(eng.parse_time, "not-a-time")
        self.assertEqual(e2.refusal, "INVALID_TIME")
        e3 = self.refusal(eng.require_ref, {"id": 1}, "record_id")
        self.assertEqual(e3.refusal, "INVALID_REFERENCE")

    def test_context_owner_data_flagged(self):
        r = self.intake(source={"kind": "SPHERE_BOOK"})
        ctx = eng.lead_context_read(self.state, {"lead_id": r["lead_id"]})
        flag = ctx["context"]["owner_details"]["flag"]
        self.assertIn("NOT OUTREACH AUTHORIZATION", flag)

    def test_fub_adapter_shape_and_stub_labels(self):
        person = {"id": 7, "name": "Synthetic FUB Person",
                  "emails": ["fub@example.invalid"],
                  "phones": ["555-0150"], "source": "Synthetic Portal"}
        args = fub_person_to_intake(person, self.mandate)
        out = eng.lead_intake(self.state, {**args, "at_time": T0})
        self.assertEqual(out["provenance_class"], "VENDOR_ASSERTED")
        stub = FUBStub()
        stub.person_created(person)
        call = stub.write_back_call(7, "follow-up", T0)
        self.assertIn("does not place", call["route"])


if __name__ == "__main__":
    unittest.main()

# Eclipse Covenant MCP · © 2026 Cyan Eclipse Technologies LLC · cetech.ai
