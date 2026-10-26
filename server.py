# Eclipse Covenant MCP
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
# Stdio MCP server: disjoint agent (14 tools) / operator surfaces.

"""Eclipse Covenant MCP — v0.1.0, P0 (consent-bound lead work).

Powered by Cyan Eclipse Technologies (cetech.ai).

Descriptor: Eclipse Covenant — consent-bound lead work for
real-estate agents: every lead carries its provenance, every outreach
is checked at send time, and every session seals into the brokerage's
defense file. It does not sell leads, hold MLS data, or perform
licensed acts.

P0 SCOPE: SYNTHETIC ONLY. Simulator send routes record, never
transmit. No live outreach, no real leads, no live API keys, no real
CRM/DNC access. Counsel HARD GATE stands before any P1 live outreach.

SURFACES (disjoint, spec 5.4): $ECLIPSE_COVENANT_SURFACE selects
"agent" (default; exactly the 14 enumerated tools) or "operator"
(mandate_create / pack_retrieve / consent_record_verify only — no
operator tool touches a single send).

Transport: MCP over stdio (newline-delimited JSON-RPC 2.0), stdlib
only. State: $ECLIPSE_COVENANT_STATE or ./data/state.json.
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import covenant_engine as eng  # noqa: E402
import signing  # noqa: E402
from covenant_engine import ToolError  # noqa: E402

STATE_PATH = Path(os.environ.get(
    "ECLIPSE_COVENANT_STATE", HERE / "data" / "state.json"))
SURFACE = os.environ.get("ECLIPSE_COVENANT_SURFACE", "agent")

AGENT_TOOLS = list(eng.TOOLS.keys())
OPERATOR_TOOLS = ["mandate_create", "pack_retrieve",
                  "consent_record_verify"]


def load_state():
    if STATE_PATH.exists():
        saved = json.loads(STATE_PATH.read_text())
        fresh = eng.empty_state()
        fresh.update(saved)
        fresh["_config"] = eng.empty_state()["_config"]
        return fresh
    return eng.empty_state()


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=1))


def pack_retrieve(state, args):
    """Operator-only: pack retrieval for the broker of record."""
    pack_id = eng.require_ref(args.get("pack_id"), "pack_id")
    pack = state["packs"].get(pack_id)
    if pack is None:
        raise ToolError("PACK_NOT_FOUND", f"no pack {pack_id}")
    return pack


def tool_list():
    names = AGENT_TOOLS if SURFACE == "agent" else OPERATOR_TOOLS
    return [{"name": n,
             "description": f"Eclipse Covenant tool {n} "
                            f"(surface: {SURFACE}, P0 synthetic)",
             "inputSchema": {"type": "object"}} for n in names]


def dispatch(state, name, args):
    allowed = AGENT_TOOLS if SURFACE == "agent" else OPERATOR_TOOLS
    if name not in allowed:
        raise ToolError("UNKNOWN_TOOL",
                        f"tool {name} is not on the {SURFACE} surface")
    if name == "mandate_create":
        return eng.mandate_create(state, args)
    if name == "pack_retrieve":
        return pack_retrieve(state, args)
    return eng.call_tool(state, name, args)


def handle(state, msg):
    method = msg.get("method")
    rid = msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "eclipse-covenant-mcp",
                           "version": "0.1.0"}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"tools": tool_list()}}
    if method == "tools/call":
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        try:
            result = dispatch(state, params.get("name"),
                              params.get("arguments") or {})
            save_state(state)
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text",
                             "text": json.dumps(result)}]}}
        except ToolError as exc:
            payload = {"refusal": exc.refusal, "body": exc.body}
            payload.update(exc.extra)
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "isError": True,
                "content": [{"type": "text",
                             "text": json.dumps(payload)}]}}
        except Exception as exc:  # noqa: BLE001 — containment, by design
            # The stdio process must NEVER die on input: any unexpected
            # engine error is contained as a named fail-closed refusal.
            payload = {"refusal": "INTERNAL_ERROR",
                       "body": f"unexpected error contained and refused "
                               f"by name: {type(exc).__name__}"}
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "isError": True,
                "content": [{"type": "text",
                             "text": json.dumps(payload)}]}}
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"unknown {method}"}}


def main():
    state = load_state()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            resp = {"jsonrpc": "2.0", "id": None, "error": {
                "code": -32600,
                "message": "invalid request: expected a JSON object"}}
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            continue
        if str(msg.get("method", "")).startswith("notifications/"):
            continue
        try:
            resp = handle(state, msg)
        except Exception as exc:  # noqa: BLE001 — containment, by design
            resp = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {
                "code": -32603,
                "message": f"internal error contained: "
                           f"{type(exc).__name__}"}}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

# Eclipse Covenant MCP · © 2026 Cyan Eclipse Technologies LLC · cetech.ai
