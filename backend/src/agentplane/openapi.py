from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentplane.api.app import create_app


def run() -> None:
    root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description="Export the AgentPlane OpenAPI document")
    parser.add_argument("--output", type=Path, default=root / "web" / "openapi.json")
    output: Path = parser.parse_args().output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(output)


if __name__ == "__main__":
    run()
