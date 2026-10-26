# Eclipse Covenant MCP
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
# Generates the SYNTHETIC fixture matrix + certificate fixture registry.

"""Fixture generator (spec 10.1): >=48 synthetic leads across
4 provenance classes x channel-grant combinations x timelines.
Every fixture is synthetic; phone numbers are in the reserved
fictional 555-01XX range. Re-running regenerates identical files.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
HEADER = "SYNTHETIC — NO REAL PERSON, NO REAL CONSENT EVENT"

GRANT_SETS = [
    {"text": {"basis": "PEWC"}, "voice": {"basis": "PEWC"},
     "email": {"basis": "EXPRESS"}},
    {"voice": {"basis": "PEWC"}},
    {"text": {"basis": "EXISTING_RELATIONSHIP_PER_PARAMS"}},
    {"email": {"basis": "EXPRESS"}},
]
TIMELINES = ["clean", "revoke_before_send", "dnc_after_import",
             "named_seller_absent"]


def main():
    fixtures, certs = [], {}
    n = 0
    for cls_idx, cls in enumerate(
            ("CERTIFIED_CAPTURE", "SELF_CAPTURED", "VENDOR_ASSERTED",
             "LEGACY_UNVERIFIED")):
        for g, grants in enumerate(GRANT_SETS):
            for t, timeline in enumerate(TIMELINES):
                n += 1
                phone = f"555-01{n:02d}"
                fx = {"fixture_id": f"FX-{n:03d}",
                      "header": HEADER,
                      "expected_class": cls, "timeline": timeline,
                      "intake": {
                          "identity": {
                              "name": f"Synthetic Person {n:02d}",
                              "phones": [phone],
                              "emails": [f"synthetic{n:02d}@example.invalid"]},
                          "channel_grants": grants,
                          "named_seller_set": [] if
                          timeline == "named_seller_absent" else
                          ["seller-alpha"],
                      }}
                if cls == "CERTIFIED_CAPTURE":
                    ref = f"CERT-FIX-{n:03d}"
                    certs[ref] = {"verify_status": "PASS",
                                  "bound_lead_key":
                                      "".join(c for c in phone
                                              if c.isdigit())}
                    fx["intake"]["certificate_ref"] = ref
                    fx["intake"]["source"] = {"kind": "OWN_FORM"}
                    fx["intake"]["capture"] = {"surface": "web_form"}
                elif cls == "SELF_CAPTURED":
                    fx["intake"]["source"] = {"kind": "OWN_FORM"}
                    fx["intake"]["capture"] = {
                        "surface": "web_form",
                        "disclosure_text_verbatim":
                            "Synthetic disclosure: I agree to be "
                            "contacted by seller-alpha.",
                        "disclosure_version": "synthetic-v1",
                        "form_snapshot_ref": f"snapshot-{n:03d}"}
                elif cls == "VENDOR_ASSERTED":
                    fx["intake"]["source"] = {
                        "kind": "PORTAL_PROGRAM",
                        "vendor_name": "Synthetic Portal Co",
                        "program_name": "synthetic-program",
                        "distribution": "UNKNOWN",
                        "program_channel": "text"}
                else:
                    fx["intake"]["source"] = {"kind": "SPHERE_BOOK"}
                    fx["intake"]["evidence_note"] = \
                        "2019 open-house sign-in sheet (synthetic) — " \
                        "not PEWC for a 2026 texting campaign"
                fixtures.append(fx)
    certs["CERT-BAD-001"] = {"verify_status": "FAIL",
                             "bound_lead_key": "5550101",
                             "reason": "issuer verification failed "
                                       "(fixture)"}
    certs["CERT-OTHER-001"] = {"verify_status": "PASS",
                               "bound_lead_key": "555019999"}
    out = {"header": HEADER, "count": len(fixtures),
           "fixtures": fixtures}
    (HERE / "fixtures" / "fixtures.json").write_text(
        json.dumps(out, indent=1))
    (HERE / "fixtures" / "cert_fixtures.json").write_text(json.dumps(
        {"header": HEADER, "certificates": certs}, indent=1))
    print(f"wrote {len(fixtures)} fixtures, {len(certs)} cert entries")


if __name__ == "__main__":
    main()

# Eclipse Covenant MCP · © 2026 Cyan Eclipse Technologies LLC · cetech.ai
