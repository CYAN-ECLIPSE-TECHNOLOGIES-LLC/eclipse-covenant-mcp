# Eclipse Covenant MCP
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
# Smoke: stdio handshake + tool list surface counts (agent/operator).

"""Run: python3 smoke.py — spawns server.py twice (agent surface,
operator surface) against temp state and asserts the disjoint
surfaces: agent = exactly 14 tools, operator = 3, no overlap of
send tools on the operator surface. SYNTHETIC ONLY."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent


def run(surface):
    env = dict(os.environ)
    env["ECLIPSE_COVENANT_SURFACE"] = surface
    env["ECLIPSE_COVENANT_STATE"] = str(
        Path(tempfile.mkdtemp()) / "state.json")
    proc = subprocess.run(
        [sys.executable, str(HERE / "server.py")], env=env,
        input='{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'
              '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n',
        capture_output=True, text=True, timeout=30)
    lines = [json.loads(x) for x in proc.stdout.splitlines() if x]
    tools = [t["name"] for t in lines[1]["result"]["tools"]]
    return tools


def main():
    agent = run("agent")
    operator = run("operator")
    assert len(agent) == 14, agent
    assert operator == ["mandate_create", "pack_retrieve",
                        "consent_record_verify"], operator
    assert not (set(agent) & {"mandate_create", "pack_retrieve"})
    print(f"smoke OK: agent surface = {len(agent)} tools; "
          f"operator surface = {len(operator)} tools; disjoint")


if __name__ == "__main__":
    main()

# Eclipse Covenant MCP · © 2026 Cyan Eclipse Technologies LLC · cetech.ai
