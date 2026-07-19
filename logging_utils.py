"""UMDA logging helpers with GitHub Actions workflow commands support.

In CI (GITHUB_ACTIONS=true): emits ::warning::/::error:: workflow commands
that show up as annotations on the PR / check run.

Locally: falls back to the classic `  WARN: ` / `  ERROR: ` print format
so umda.sh / local builds keep working unchanged.
"""
import os
import sys


def _emit(level: str, msg: str, file: str | None = None, line: int | None = None, col: int | None = None) -> None:
    # GitHub workflow commands don't allow newlines or stray ':' in the message
    safe_msg = msg.replace("\n", "%0A").replace("\r", "%0D")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        cmd = f"::{level}"
        params = []
        if file:
            params.append(f"file={file}")
        if line:
            params.append(f"line={line}")
        if col:
            params.append(f"col={col}")
        if params:
            cmd += " " + ",".join(params)
        print(f"{cmd}::{safe_msg}")
    else:
        prefix = "  WARN" if level == "warning" else "  ERROR"
        stream = sys.stderr if level == "error" else sys.stdout
        loc = ""
        if file and line:
            loc = f" [{file}:{line}]"
        elif file:
            loc = f" [{file}]"
        print(f"{prefix}: {msg}{loc}", file=stream)


def warn(msg: str, file: str | None = None, line: int | None = None) -> None:
    _emit("warning", msg, file, line)


def error(msg: str, file: str | None = None, line: int | None = None) -> None:
    _emit("error", msg, file, line)
