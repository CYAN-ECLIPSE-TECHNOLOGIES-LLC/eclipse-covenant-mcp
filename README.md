# Eclipse Covenant MCP

**Powered by Cyan Eclipse Technologies** — https://cetech.ai

**Eclipse Covenant — consent-bound lead work for real-estate agents: every lead carries its provenance, every outreach is checked at send time, and every session seals into the brokerage's defense file. It does not sell leads, hold MLS data, or perform licensed acts.**

Status: **built-uncertified / bench-pending** (P0, v0.1.0). Verity benches separately; no certification is claimed here.

## Honesty statement (normative, spec §10)

CETech holds **no real-estate license, no MLS membership, and no book of leads. NO real lead book exists.** All P0 evidence is synthetic by construction and labeled so. Fixture phone numbers are in the reserved fictional 555-01XX range. Path to real evidence = design partner (D4) at P1 + counsel (D3).

**Counsel HARD GATE:** TCPA + fair-housing counsel review sets the P1 `RuleParameterSet` + Screen vocabularies + pilot-state overlays **before any P1 live outreach**. P0 is synthetic only: no live sends, no live API keys, no real leads, no real CRM data, no real DNC registry access.

## What it does

- **Covenant Record** — Herald-signed (simulator-mode HMAC) consent record per lead per mandate, hash-chained, with provenance class `CERTIFIED_CAPTURE` / `SELF_CAPTURED` / `VENDOR_ASSERTED` / `LEGACY_UNVERIFIED` — never blended; class degrades monotonically on failed evidence, rises only via a fresh verified certificate. `VENDOR_ASSERTED` defaults to mandate posture `NARROW_FIRST_CONTACT_ONLY`. `LEGACY_UNVERIFIED` permits no automated outreach.
- **Covenant Gate** — inside the contact tools themselves, at send time, in order: record exists under this mandate → per-channel scope (voice consent ≠ text consent → `CONSENT_SCOPE_MISMATCH`) → named-seller coverage → no open revocation (unresolved scope blocks **all** channels) → **fresh DNC scrub at send** (fixture lists loaded from disk per send) → fair-housing guards. First failure stops the pipeline; the transcript records `NOT_RUN_UPSTREAM_REFUSAL` for later steps.
- **Covenant Pack** — Blackbox-form evidence pack addressed to the broker of record: every send **and every refusal** sealed as a hash-chained, Herald-signed entry with its full check transcript.
- **Covenant Screen** — Copy Screen + Criteria Screen over a closed, versioned vocabulary: findings with spans, text returned **unmodified**, never silent rewrites. Canonical case: "young families near good schools" → `PROTECTED_CRITERIA_REFUSED` + `PROXY_CRITERIA_REFUSED` (proxy named).
- **Consent Record Verifier** (`consent_record_verify`) — offline RECORD/PACK verification on a separate code path: class-vs-evidence consistency, chain/signature checks, honor-deadline arithmetic re-checked under the rule version pinned at send time.

## Design law (spec §3)

Consent/revocation rules are **versioned data parameters, never code constants**. All rule values live in `data/rule_packs/SYNTHETIC_DEFAULT-v1.json` (labeled `SYNTHETIC_DEFAULT — NOT COUNSEL-SET`): PEWC scope semantics, revocation keywords + plain-language set, 10-business-day honor window, single template-pinned clarification, revoke-all tail status (`DELAYED_UNTIL_2027_01_31`), vendor posture default, retention horizon. A rule change = a new pack version, never a code patch. Missing pack → `RULE_PACK_UNAVAILABLE`; pack past its data-carried `stale_after` → `RULE_PACK_STALE` (both fail closed). Certificates are **evidence, never authorization**.

## Structural law (spec §9)

Never holds or redistributes MLS data; no scraping routes at any phase; no lead resale/transfer/pooling (`LEAD_EXPORT_REFUSED`, incl. deep-key-scanned argument shapes); no licensed acts (`LICENSED_ACT_REFUSED`); no consent manufacture; no bulk-send bypass, no `skip_checks` (`GATE_BYPASS_REFUSED`).

## Follow Up Boss anchor — read the labels

FUB is the P0 CRM adapter **shape** (`fub_adapter.py`, local fixture stub over FUB surface nouns: people/events/calls/textMessages/tasks). **Correction C1: the Follow Up Boss API does not place calls.** `outreach_call_place` is a **simulator dialer STUB** at P0; live voice = P1 provider route. `lead_context_read` is **fixture-only** at P0 (correction C2). The live CRM Connector is P1, under a partner brokerage's own grant.

## Tools (14, closed by enumeration)

Intake/record: `lead_intake`, `consent_attach_certificate`, `consent_record_read`, `consent_history`, `revocation_record`, `lead_context_read` · Outreach (the only send path): `outreach_text`, `outreach_call_place`, `outreach_email`, `followup_sequence_plan`, `showing_schedule` · Screens + verifier: `copy_screen`, `criteria_screen`, `consent_record_verify`.

Operator surface (disjoint, `$ECLIPSE_COVENANT_SURFACE=operator`): `mandate_create`, `pack_retrieve`, `consent_record_verify` — no operator tool touches a single send.

## Run

```bash
python3 generate_fixtures.py          # 64 synthetic fixtures + cert registry
python3 -m unittest discover -s tests # test suite
python3 smoke.py                      # stdio surface smoke
ECLIPSE_COVENANT_STATE=/tmp/state.json python3 server.py
```

Stdlib only. No network code paths anywhere in the tree (test-enforced).
