# Eclipse Covenant MCP
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
# Follow Up Boss adapter SHAPE + local stub (P0, fixture-only).

"""Follow Up Boss (FUB) adapter shape — P0 fixture mode only.

Anchor rationale (spec 10.1): FUB exposes a full REST API + webhooks
and 250+ lead-source integrations. Fixture resources mirror FUB's
surface nouns (people, events, calls, textMessages, tasks) in
stylized form with NO real data.

STUB LABELS (binding):
- FUBStub is a LOCAL in-process fixture stub. It opens no connection.
- The FUB API does NOT place outbound calls (spec correction C1).
  Voice placement is a P1 provider route; P0 uses the simulator
  dialer stub in covenant_engine. This adapter never dials.
- The live CRM Connector (hosted bridge holding the brokerage's own
  CRM grant) is P1 work under a partner brokerage's grant.
"""


def fub_person_to_intake(person, mandate_id):
    """Map a FUB-shaped `people` resource / personCreated webhook
    payload to a lead_intake argument set. Foreign-authored payload
    content is data with provenance (Law #1): it is mapped into
    fields, never executed or treated as instructions."""
    emails = [{"value": e} if isinstance(e, str) else e
              for e in person.get("emails", [])]
    phones = [{"value": p} if isinstance(p, str) else p
              for p in person.get("phones", [])]
    return {
        "mandate_id": mandate_id,
        "crm_ref": f"fub-person-{person.get('id')}",
        "identity": {
            "name": person.get("name"),
            "emails": [e.get("value") for e in emails],
            "phones": [p.get("value") for p in phones],
        },
        "source": {"kind": "CRM_IMPORT",
                   "vendor_name": person.get("source"),
                   "distribution": "UNKNOWN"},
    }


class FUBStub:
    """Local fixture stub of the FUB surface: stores people/events
    and accepts calls/textMessages write-backs. No transmission."""

    def __init__(self):
        self.people = {}
        self.events = []
        self.calls = []
        self.text_messages = []
        self.tasks = []
        self.label = "LOCAL FIXTURE STUB — no live FUB connection"

    def person_created(self, person):
        self.people[person["id"]] = person
        self.events.append({"type": "personCreated", "person": person})
        return person

    def write_back_text(self, person_id, body, at):
        entry = {"personId": person_id, "body": body, "at": at,
                 "route": "STUB WRITE-BACK — not sent via FUB"}
        self.text_messages.append(entry)
        return entry

    def write_back_call(self, person_id, purpose, at):
        entry = {"personId": person_id, "purpose": purpose, "at": at,
                 "route": "STUB WRITE-BACK — FUB API does not place "
                          "calls; no call was placed"}
        self.calls.append(entry)
        return entry

# Eclipse Covenant MCP · © 2026 Cyan Eclipse Technologies LLC · cetech.ai
