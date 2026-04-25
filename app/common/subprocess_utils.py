from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional


def run_cmd(args: List[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    """Run a command without shell expansion and return captured output."""
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def require_success(result: subprocess.CompletedProcess, action: str) -> str:
    """Return stdout or raise RuntimeError with stderr context."""
    if result.returncode != 0:
        raise RuntimeError(f"{action} failed: {result.stderr.strip()}")
    return result.stdout.strip()
