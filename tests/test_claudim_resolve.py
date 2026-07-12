import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "claudim-resolve.py"
CATALOG = {
    "models": [
        {
            "id": "id/kimi",
            "agent_name": "delegate-kimi-k2-7-code",
            "display_name": "Kimi K2.7 Code",
            "policy": "delegate",
            "capabilities": ["coding"],
            "aliases": ["kimi-k2-7-code"],
        },
        {
            "id": "id/kimi-lite",
            "agent_name": "delegate-kimi-lite",
            "display_name": "Kimi Lite",
            "policy": "delegate",
            "capabilities": ["fast"],
            "aliases": ["kimi-lite"],
        },
    ]
}


def _resolve(query: str) -> dict:
    proc = subprocess.run(
        ["python3", str(SCRIPT), query],
        input=json.dumps(CATALOG),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(proc.stdout)


def test_resolves_local_override_before_catalog() -> None:
    assert _resolve("opus")["policy"] == "override"


def test_resolves_human_name_and_never_picks_ambiguity() -> None:
    assert _resolve("Kimi K2.7 Code")["id"] == "id/kimi"
    ambiguous = _resolve("kimi")
    assert ambiguous["status"] == "ambiguous"
    assert ambiguous["id"] is None
