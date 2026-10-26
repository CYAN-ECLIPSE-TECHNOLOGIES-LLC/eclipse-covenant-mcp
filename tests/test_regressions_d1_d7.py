# Eclipse Covenant MCP
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
# Fix-round regressions D1-D7: Verity bench shapes replicated
# BYTE-FOR-BYTE from the retained scorecard harness
# (eclipse-covenant-mcp-2026-10-25.json, FAILED 28/35).

"""SYNTHETIC ONLY — all leads, numbers (555-01XX), and consent events
in this suite are fixtures. No live sends, no real data."""
import json
import os
import shutil
import subprocess
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


def officer_sig(b="brokerage-1", o="officer-1"):
    return signing.sign({"mandate_officer": o, "brokerage_id": b})


def fresh():
    tmp = Path(tempfile.mkdtemp())
    dnc = tmp / "dnc.json"
    shutil.copy(HERE / "data" / "dnc_fixture.json", dnc)
    st = eng.empty_state({"dnc_path": str(dnc)})
    m = eng.mandate_create(st, {"brokerage_id": "brokerage-1",
        "officer": "officer-1", "officer_sig": officer_sig(),
        "seats": [SELLER],
        "broker_of_record": "Broker Of Record (synthetic)"})
    return st, m["mandate_id"], dnc, tmp


# Bench harness intake shapes, byte-for-byte (verity_covenant_bench.py).
def intake(st, mid, phone="555-0120", grants=None, sellers=(SELLER,),
           source=None, capture=None, cert=None, **kw):
    a = {"mandate_id": mid, "identity": {"name": "Synthetic Lead",
        "phones": [phone], "emails": ["lead@example.invalid"]},
        "source": source or {"kind": "OWN_FORM"},
        "capture": capture or {}, "channel_grants": grants or {},
        "named_seller_set": list(sellers), "at_time": T0}
    if cert:
        a["certificate_ref"] = cert
    a.update(kw)
    return eng.lead_intake(st, a)


def selfcap(st, mid, phone="555-0120", grants=None, **kw):
    return intake(st, mid, phone=phone,
        grants=grants if grants is not None else
        {"text": {"basis": "PEWC"}, "voice": {"basis": "PEWC"},
         "email": {"basis": "EXPRESS"}},
        capture={"surface": "web_form",
                 "disclosure_text_verbatim": "Synthetic disclosure",
                 "disclosure_version": "synthetic-v1",
                 "form_snapshot_ref": "snap-1"}, **kw)


def send(st, lead, mid, tool="outreach_text", **kw):
    a = {"lead_id": lead, "mandate_id": mid,
         "sending_identity": SELLER, "at_time": T0,
         "body": "Hello, following up on your inquiry."}
    a.update(kw)
    return eng.call_tool(st, tool, a)


def expect_refusal(fn):
    try:
        out = fn()
        return ("NO_REFUSAL", out)
    except ToolError as e:
        return (e.refusal, e.body)
    except Exception as e:  # raw escape = the defect under test
        return (f"RAW:{type(e).__name__}", str(e)[:200])


class TestD1D2Reintake(unittest.TestCase):
    def test_d1_reintake_revocation_persistence(self):
        # Bench HOSTILE_reintake_revocation_persistence, byte-for-byte.
        st, mid, dnc, tmp = fresh()
        r = selfcap(st, mid)
        eng.revocation_record(st, {"lead_id": r["lead_id"],
            "received_via_channel": "text", "raw_payload": "STOP",
            "at_time": T0})
        blocked, _ = expect_refusal(lambda: send(st, r["lead_id"], mid))
        self.assertEqual(blocked, "CONSENT_REVOKED")
        r2 = selfcap(st, mid)  # duplicate webhook / re-import
        self.assertEqual(r2["record_id"], r["record_id"])
        rec = eng.consent_record_read(st, {"record_id": r["record_id"]})
        self.assertEqual(rec["revocation_state"], "SCOPE_UNRESOLVED")
        self.assertEqual(len(rec["revocations"]), 1)
        self.assertEqual(rec["revocations"][0]["raw_payload_verbatim"],
                         "STOP")
        after, _ = expect_refusal(lambda: send(st, r["lead_id"], mid))
        self.assertEqual(after, "CONSENT_REVOKED")

    def test_d2_reintake_class_monotone_no_evidence_drop(self):
        # Bench HOSTILE_reintake_class_downgrade, byte-for-byte.
        st, mid, dnc, tmp = fresh()
        rc = intake(st, mid, phone="555-0101", cert="CERT-FIX-001",
                    grants={"text": {"basis": "PEWC"}})
        self.assertEqual(rc["provenance_class"], "CERTIFIED_CAPTURE")
        rd = intake(st, mid, phone="555-0101",
            source={"kind": "PORTAL_PROGRAM",
                    "vendor_name": "Synthetic Portal Co",
                    "distribution": "UNKNOWN",
                    "program_channel": "text"},
            grants={"text": {"basis": "EXPRESS"}})
        self.assertEqual(rd["provenance_class"], "CERTIFIED_CAPTURE")
        rec = eng.consent_record_read(st, {"record_id": rc["record_id"]})
        self.assertEqual(rec["provenance_class"], "CERTIFIED_CAPTURE")
        self.assertEqual(len(rec["certificate_evidence"]), 1)
        self.assertEqual(rec["certificate_evidence"][0]["ref"],
                         "CERT-FIX-001")
        self.assertEqual(
            rec["certificate_evidence"][0]["verify_status"], "PASS")
        self.assertEqual(rec["version"], 2)


class TestD3PlainLanguage(unittest.TestCase):
    def test_d3_offvocab_revocation_unresolved_blocks_all(self):
        # Bench H4_plain_language_offvocab, byte-for-byte payload.
        st, mid, dnc, tmp = fresh()
        r = selfcap(st, mid)
        got, body = expect_refusal(lambda: eng.revocation_record(st, {
            "lead_id": r["lead_id"], "received_via_channel": "email",
            "raw_payload":
                "I withdraw my consent — do not reach out to me again",
            "at_time": T0}))
        self.assertEqual(got, "NO_REFUSAL",
                         "off-vocab revocation must be recorded, "
                         "never refused as not-a-revocation")
        out = body
        self.assertEqual(out["normalized_scope"], "UNRESOLVED")
        rec = eng.consent_record_read(st, {"record_id": r["record_id"]})
        self.assertEqual(rec["revocation_state"], "SCOPE_UNRESOLVED")
        after, _ = expect_refusal(lambda: send(st, r["lead_id"], mid))
        self.assertEqual(after, "CONSENT_REVOKED")
        after_v, _ = expect_refusal(lambda: send(
            st, r["lead_id"], mid, tool="outreach_call_place",
            body=None, call_purpose_class="fu"))
        self.assertEqual(after_v, "CONSENT_REVOKED")

    def test_d3_not_a_revocation_is_gone(self):
        src = (HERE / "covenant_engine.py").read_text()
        self.assertNotIn("NOT_A_REVOCATION", src)


class TestD5CorruptPack(unittest.TestCase):
    def test_d5_corrupt_pack_named_refusal(self):
        # Bench RulePack_corrupt_f, byte-for-byte corrupt content.
        st, mid, dnc, tmp = fresh()
        badp = tmp / "bad.json"
        badp.write_text("{not json")
        st2, mid2, dnc2, _ = fresh()
        st2["_config"]["rule_pack_path"] = str(badp)
        got, _ = expect_refusal(lambda: selfcap(st2, mid2))
        self.assertEqual(got, "RULE_PACK_UNAVAILABLE")
        self.assertFalse(got.startswith("RAW:"))


class TestD6VerifierPinnedHash(unittest.TestCase):
    def test_d6_archive_pack_hash_edit_fails_verification(self):
        # Bench Verifier_pinned_hash, byte-for-byte tamper.
        st, mid, dnc, tmp = fresh()
        packdir = tmp / "packs"
        packdir.mkdir()
        orig = json.loads((HERE / "data" / "rule_packs" /
                           "SYNTHETIC_DEFAULT-v1.json").read_text())
        (packdir / "SYNTHETIC_DEFAULT-v1.json").write_text(
            json.dumps(orig))
        st["_config"]["rule_pack_dir"] = str(packdir)
        st["_config"]["rule_pack_path"] = str(
            packdir / "SYNTHETIC_DEFAULT-v1.json")
        r = selfcap(st, mid)
        eng.revocation_record(st, {"lead_id": r["lead_id"],
            "received_via_channel": "text", "raw_payload": "STOP",
            "at_time": T0})
        pack = list(st["packs"].values())[0]
        pinned_hash = pack["revocations"][0]["scope_rule_version"]["hash"]
        honest = eng.consent_record_verify(
            st, {"mode": "PACK", "pack_id": pack["pack_id"]})
        self.assertEqual(honest["verdict"], "VERIFIED", honest)
        tampered = dict(orig)
        tampered["note"] = "TAMPERED — values silently replaced"
        (packdir / "SYNTHETIC_DEFAULT-v1.json").write_text(
            json.dumps(tampered))
        loaded = eng.load_rule_pack_by_ref(
            st, pack["revocations"][0]["scope_rule_version"])
        hash_now = signing.content_hash(
            {k: v for k, v in loaded.items()
             if not k.startswith("_")}) if loaded else None
        self.assertNotEqual(hash_now, pinned_hash)
        vt = eng.consent_record_verify(
            st, {"mode": "PACK", "pack_id": pack["pack_id"]})
        self.assertEqual(vt["verdict"], "FAILED", vt)
        self.assertTrue(any("pinned" in d and "hash" in d
                            for d in vt["discrepancies"]),
                        vt["discrepancies"])


class TestD7ExportDetector(unittest.TestCase):
    def test_d7_bulk_string_and_paginated_sweep(self):
        # Bench Export_d probes, byte-for-byte shapes.
        st, mid, dnc, tmp = fresh()
        r = selfcap(st, mid)
        got, _ = expect_refusal(lambda: eng.consent_record_read(
            st, {"record_id": r["record_id"], "bulk": True}))
        self.assertEqual(got, "LEAD_EXPORT_REFUSED")
        got, _ = expect_refusal(lambda: eng.consent_record_read(
            st, {"record_id": r["record_id"], "bulk": "true"}))
        self.assertEqual(got, "LEAD_EXPORT_REFUSED")
        sweep = 0
        sweep_refusal = None
        for _i in range(30):
            try:
                eng.consent_record_read(st, {"record_id": r["record_id"]})
                sweep += 1
            except ToolError as e:
                sweep_refusal = e.refusal
                break
        self.assertLess(sweep, 30)
        self.assertEqual(sweep_refusal, "LEAD_EXPORT_REFUSED")
        self.assertTrue(any(
            e.get("kind") == "EXPORT_SWEEP_DETECTED"
            for e in st["operator_log"]))


class TestD4Fuzz(unittest.TestCase):
    def test_d4_bench_fuzz_set_no_raw_escapes(self):
        # Bench Fuzz_all_tools_i cases, byte-for-byte (24 cases).
        st, mid, dnc, tmp = fresh()
        r = selfcap(st, mid)
        plan = eng.followup_sequence_plan(st, {
            "lead_id": r["lead_id"], "mandate_id": mid,
            "steps": [{"channel": "text", "body": "s"}], "at_time": T0})

        def bsend(lead, **kw):
            return send(st, lead, mid, **kw)

        cases = {
         "intake channel_grants=list": lambda: eng.lead_intake(st, {"mandate_id": mid, "identity": {"phones": ["555-0170"]}, "source": {}, "capture": {}, "channel_grants": ["text"], "named_seller_set": [SELLER]}),
         "intake named_seller_set=int": lambda: eng.lead_intake(st, {"mandate_id": mid, "identity": {"phones": ["555-0171"]}, "source": {}, "capture": {}, "channel_grants": {}, "named_seller_set": 42}),
         "intake identity=list": lambda: eng.lead_intake(st, {"mandate_id": mid, "identity": [1, 2], "source": {}, "capture": {}}),
         "intake grants basis bogus": lambda: eng.lead_intake(st, {"mandate_id": mid, "identity": {"phones": ["555-0172"]}, "source": {}, "capture": {}, "channel_grants": {"text": {"basis": "VERBAL_OK"}}}),
         "send body=dict": lambda: bsend(r["lead_id"], body={"x": 1}),
         "send lead_id=dict": lambda: bsend({"x": 1}),
         "send at_time garbage": lambda: bsend(r["lead_id"], at_time="not-a-time"),
         "copy_screen text=int": lambda: eng.copy_screen(st, {"text": 123}),
         "copy_screen overlong": lambda: eng.copy_screen(st, {"text": "x" * 200000}),
         "criteria criteria=list": lambda: eng.criteria_screen(st, {"criteria": ["young families"]}),
         "revocation raw=int": lambda: eng.revocation_record(st, {"lead_id": r["lead_id"], "received_via_channel": "text", "raw_payload": 123}),
         "revocation channel bogus": lambda: eng.revocation_record(st, {"lead_id": r["lead_id"], "received_via_channel": "fax", "raw_payload": "STOP"}),
         "attach record_id=int": lambda: eng.consent_attach_certificate(st, {"record_id": 123, "trustedform_cert_url": "CERT-FIX-001"}),
         "verify record=list": lambda: eng.consent_record_verify(st, {"mode": "RECORD", "record": [1, 2]}),
         "verify pack=list": lambda: eng.consent_record_verify(st, {"mode": "PACK", "pack": "nope"}),
         "verify mode bogus": lambda: eng.consent_record_verify(st, {"mode": "AUDIT"}),
         "history lead_id=None": lambda: eng.consent_history(st, {"lead_id": None}),
         "seq step_index=str": lambda: eng.followup_sequence_plan(st, {"mode": "run_step", "plan_id": plan["plan_id"], "step_index": "zero", "sending_identity": SELLER}),
         "seq steps=dict": lambda: eng.followup_sequence_plan(st, {"lead_id": r["lead_id"], "steps": {"channel": "text"}}),
         "showing lead_id=bool": lambda: eng.showing_schedule(st, {"lead_id": True}),
         "context lead_id=list": lambda: eng.lead_context_read(st, {"lead_id": ["x"]}),
         "read record_id=spaces": lambda: eng.consent_record_read(st, {"record_id": "  "}),
         "call_tool args=None": lambda: eng.call_tool(st, "outreach_text", None),
         "unknown tool": lambda: eng.call_tool(st, "rm_rf", {}),
        }
        self.assertEqual(len(cases), 24)
        raws = []
        for name, fn in cases.items():
            got, _ = expect_refusal(fn)
            if got.startswith("RAW:"):
                raws.append(f"{name}->{got}")
        self.assertEqual(raws, [])

    def test_d4_formerly_raw_shapes_refuse_by_name(self):
        st, mid, dnc, tmp = fresh()
        got, _ = expect_refusal(lambda: eng.lead_intake(st, {
            "mandate_id": mid, "identity": {"phones": ["555-0170"]},
            "source": {}, "capture": {}, "channel_grants": ["text"],
            "named_seller_set": [SELLER]}))
        self.assertEqual(got, "INVALID_ARGUMENT")
        got, _ = expect_refusal(lambda: eng.lead_intake(st, {
            "mandate_id": mid, "identity": {"phones": ["555-0171"]},
            "source": {}, "capture": {}, "channel_grants": {},
            "named_seller_set": 42}))
        self.assertEqual(got, "INVALID_ARGUMENT")
        got, _ = expect_refusal(lambda: eng.consent_record_verify(
            st, {"mode": "RECORD", "record": [1, 2]}))
        self.assertEqual(got, "INVALID_ARGUMENT")
        got, _ = expect_refusal(lambda: eng.consent_record_verify(
            st, {"mode": "PACK", "pack": "nope"}))
        self.assertEqual(got, "INVALID_ARGUMENT")

    def test_d4_stdio_process_survives_fuzz_set(self):
        # Bench transport-fatal shape: lead_intake channel_grants=
        # ["text"] via server.py with a valid mandate killed the
        # stdio process (0 responses). Seed exactly as the bench
        # server harness seeds, then drive the full fuzz set.
        tmp = Path(tempfile.mkdtemp())
        state_path = tmp / "state.json"
        st = eng.empty_state()
        sig = signing.sign({"mandate_officer": "officer-1",
                            "brokerage_id": "brokerage-1"})
        m = eng.mandate_create(st, {"brokerage_id": "brokerage-1",
            "officer": "officer-1", "officer_sig": sig,
            "seats": [SELLER],
            "broker_of_record": "Broker Of Record (synthetic)"})
        r = eng.lead_intake(st, {"mandate_id": m["mandate_id"],
            "identity": {"name": "S", "phones": ["555-0120"],
                         "emails": ["s@example.invalid"]},
            "source": {"kind": "OWN_FORM"},
            "capture": {"disclosure_text_verbatim": "d",
                        "form_snapshot_ref": "snap"},
            "channel_grants": {"text": {"basis": "PEWC"}},
            "named_seller_set": [SELLER], "at_time": T0})
        eng.call_tool(st, "outreach_text", {
            "lead_id": r["lead_id"], "mandate_id": m["mandate_id"],
            "sending_identity": SELLER, "body": "hi", "at_time": T0})
        state_path.write_text(json.dumps(st))

        def call(name, args, i):
            return json.dumps({"jsonrpc": "2.0", "id": i,
                "method": "tools/call",
                "params": {"name": name, "arguments": args}})

        lines = [
            '{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
            # the process-killing shape from the bench:
            call("lead_intake", {"mandate_id": m["mandate_id"],
                "identity": {"phones": ["555-0170"]}, "source": {},
                "capture": {}, "channel_grants": ["text"],
                "named_seller_set": [SELLER]}, 2),
            call("lead_intake", {"mandate_id": m["mandate_id"],
                "identity": {"phones": ["555-0171"]}, "source": {},
                "capture": {}, "channel_grants": {},
                "named_seller_set": 42}, 3),
            call("outreach_text", ["not", "a", "dict"], 4),
            call("outreach_text", "a string", 5),
            '{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"outreach_text","arguments":42}}',
            call("consent_record_verify",
                 {"mode": "RECORD", "record": [1, 2]}, 7),
            call("consent_record_verify",
                 {"mode": "PACK", "pack": "nope"}, 8),
            '{"jsonrpc":"2.0","id":9,"method":"bogus/method"}',
            "[1,2,3]",
            call("consent_history", {"lead_id": r["lead_id"]}, 10),
        ]
        env = dict(os.environ)
        env["ECLIPSE_COVENANT_SURFACE"] = "agent"
        env["ECLIPSE_COVENANT_STATE"] = str(state_path)
        p = subprocess.run([sys.executable, str(HERE / "server.py")],
                           env=env, input="\n".join(lines) + "\n",
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr[-500:])
        responses = [json.loads(x) for x in p.stdout.splitlines() if x]
        # one response per request line (11 requests, 11 responses)
        self.assertEqual(len(responses), len(lines), p.stdout)
        by_id = {resp.get("id"): resp for resp in responses}
        killer = by_id[2]["result"]["content"][0]["text"]
        self.assertIn("INVALID_ARGUMENT", killer)
        self.assertTrue(by_id[2]["result"]["isError"])
        # final valid call still answered correctly after the fuzz set
        hist = by_id[10]["result"]["content"][0]["text"]
        self.assertIn(r["lead_id"], hist)


if __name__ == "__main__":
    unittest.main()

# Eclipse Covenant MCP · © 2026 Cyan Eclipse Technologies LLC · cetech.ai
