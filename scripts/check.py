"""Run the repository's platform-neutral quality checks."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from shutil import which


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path) -> None:
    print(f"\n> {' '.join(command)}", flush=True)
    executable = which(command[0])
    if executable is None:
        raise RuntimeError(f"Required executable not found: {command[0]}")
    subprocess.run([executable, *command[1:]], cwd=cwd, check=True)


def verify_generated_contracts() -> None:
    with tempfile.TemporaryDirectory(prefix="agentplane-contracts-") as temp_directory:
        temp = Path(temp_directory)
        openapi_output = temp / "openapi.json"
        schema_output = temp / "schema.d.ts"
        run(
            [
                "uv",
                "run",
                "agentplane-export-openapi",
                "--output",
                str(openapi_output),
            ],
            ROOT / "backend",
        )
        run(
            [
                "pnpm",
                "exec",
                "openapi-typescript",
                str(openapi_output),
                "-o",
                str(schema_output),
            ],
            ROOT / "web",
        )
        expected = (
            (ROOT / "web" / "openapi.json", openapi_output),
            (ROOT / "web" / "src" / "api" / "schema.d.ts", schema_output),
        )
        drifted = [
            str(committed)
            for committed, generated in expected
            if committed.read_bytes() != generated.read_bytes()
        ]
        if drifted:
            raise RuntimeError(f"Generated API contracts have drifted: {', '.join(drifted)}")


def main() -> int:
    setup_checks = [
        (["uv", "sync", "--locked", "--all-groups"], ROOT / "backend"),
        (["pnpm", "install", "--frozen-lockfile"], ROOT / "web"),
    ]
    checks = [
        (["uv", "run", "ruff", "check", "."], ROOT / "backend"),
        (["uv", "run", "ruff", "format", "--check", "."], ROOT / "backend"),
        (["uv", "run", "pyright"], ROOT / "backend"),
        (["uv", "run", "pytest"], ROOT / "backend"),
        (["pnpm", "lint"], ROOT / "web"),
        (["pnpm", "typecheck"], ROOT / "web"),
        (["pnpm", "test"], ROOT / "web"),
        (["pnpm", "build"], ROOT / "web"),
    ]
    try:
        for command, cwd in setup_checks:
            run(command, cwd)
        verify_generated_contracts()
        for command, cwd in checks:
            run(command, cwd)
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"\nCHECK FAILED: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            return exc.returncode
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
