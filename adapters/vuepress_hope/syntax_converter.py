#!/usr/bin/env python3
"""
VuePress Hope syntax converter.

Reads swap_list.yml and applies rules to convert UMDA/MkDocs syntax
to VuePress Hope hint containers and components.

Rule types:
  block   - emoji/admonition header + indented body -> ::: container
  regex   - simple regex substitution
  include - inline file content
  tabs    - tab blocks (code handler)
"""
from __future__ import annotations

import re
import yaml
from pathlib import Path

from logging_utils import error

_SWAP_LIST = Path(__file__).parent / "swap_list.yml"
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _render_inline_title_md(text: str) -> str:
    """Render a small subset of inline markdown inside admonition titles.

    VuePress hint container titles are not parsed as markdown by default,
    so convert bold syntax to inline HTML to preserve expected rendering.
    """
    return _BOLD_RE.sub(r"<strong>\1</strong>", text)


def _parse_pattern(from_str: str) -> re.Pattern:
    """Parse r'...' string from yaml into compiled re.Pattern."""
    s = from_str.strip()
    if s.startswith("r'"):
        s = s[2:]
    elif s.startswith('r"'):
        s = s[2:]
    if s.endswith("'") or s.endswith('"'):
        s = s[:-1]
    return re.compile(s, re.MULTILINE)


def _load_swap_list(path: Path = _SWAP_LIST) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _apply_block_rule(content: str, pattern: re.Pattern, container_header: str) -> str:
    """Convert block with header matching pattern + indented body into ::: container."""
    lines = content.split("\n")
    result = []
    i = 0

    while i < len(lines):
        m = pattern.match(lines[i])
        if m:
            header = container_header
            for gi in range(1, len(m.groups()) + 1):
                group_value = _render_inline_title_md(m.group(gi) or "")
                header = header.replace(f"\\{gi}", group_value)
            header_line = header.rstrip()
            result.append(header_line)

            # Close with the same fence length as opening (e.g. :::: details -> ::::).
            close_fence = ":::"
            fence_match = re.match(r"^\s*(:+)", header_line)
            if fence_match:
                close_fence = ":" * len(fence_match.group(1))
            i += 1

            if i < len(lines) and lines[i].strip() == "":
                result.append("")
                i += 1

            while i < len(lines) and (
                lines[i].startswith("    ")
                or lines[i].startswith("\t")
                or lines[i].strip() == ""
            ):
                line = lines[i]
                if line.strip() == "":
                    j = i + 1
                    while j < len(lines) and lines[j].strip() == "":
                        j += 1
                    if j < len(lines) and (
                        lines[j].startswith("    ") or lines[j].startswith("\t")
                    ):
                        result.append("")
                        i += 1
                    else:
                        break
                else:
                    if line.startswith("    "):
                        result.append(line[4:])
                    elif line.startswith("\t"):
                        result.append(line[1:])
                    else:
                        result.append(line)
                    i += 1

            result.append(close_fence)
            result.append("")
        else:
            result.append(lines[i])
            i += 1

    return "\n".join(result)


def _apply_include(content: str, pattern: re.Pattern, md_file: Path, src_root: Path) -> str:
    """Replace include marker with actual file content."""

    def replacer(m):
        file_path = m.group(1).strip()
        line_no = content[:m.start()].count("\n") + 1
        target = src_root / file_path
        if not target.exists():
            parent = target.parent
            if parent.exists():
                for f in parent.iterdir():
                    if f.name.lower() == target.name.lower():
                        target = f
                        break
        if target.exists():
            return target.read_text(encoding="utf-8").rstrip()

        error(f"include not found: {file_path}", file=str(md_file), line=line_no)
        return m.group(0)

    return pattern.sub(replacer, content)


def _apply_tabs(content: str) -> str:
    """Convert tab blocks to VuePress ::: tabs format."""
    lines = content.split("\n")
    result = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == "🗂️":
            i += 1
            tabs = []
            current_name = None
            current_body = []

            while i < len(lines):
                line = lines[i]
                m = re.match(r"^\d+\.\s+(.+)", line)
                if m:
                    if current_name is not None:
                        tabs.append((current_name, current_body))
                    current_name = m.group(1).strip()
                    current_body = []
                    i += 1
                elif line == "" or line[0].isspace():
                    if current_name is not None:
                        current_body.append(line)
                    i += 1
                else:
                    break

            if current_name is not None:
                tabs.append((current_name, current_body))

            if tabs:
                result.append("::: tabs")
                for tab_name, body in tabs:
                    result.append(f"@tab {tab_name}")
                    for bline in body:
                        if bline.startswith("    "):
                            result.append(bline[4:])
                        elif bline.startswith("\t"):
                            result.append(bline[1:])
                        else:
                            result.append(bline)
                result.append(":::")
                result.append("")
        else:
            result.append(lines[i])
            i += 1
    return "\n".join(result)


def _apply_all_rules(content: str, rules: dict, md_file: Path, src_root: Path) -> str:
    """Apply all swap_list rules in deterministic order."""

    for _, rule in rules.items():
        if rule.get("type") == "include":
            convert = rule.get("convert", {})
            from_raw = convert.get("from", "")
            if from_raw:
                pattern = _parse_pattern(from_raw)
                content = _apply_include(content, pattern, md_file, src_root)

    for _, rule in rules.items():
        if rule.get("type") == "block":
            convert = rule.get("convert", {})
            from_raw = convert.get("from", "")
            to_raw = convert.get("to", "")
            if from_raw and to_raw:
                pattern = _parse_pattern(from_raw)
                content = _apply_block_rule(content, pattern, to_raw)

    for _, rule in rules.items():
        if rule.get("type") == "regex":
            convert = rule.get("convert", {})
            from_raw = convert.get("from", "")
            to_raw = convert.get("to", "")
            if from_raw and to_raw:
                pattern = _parse_pattern(from_raw)
                content = pattern.sub(to_raw, content)

    for _, rule in rules.items():
        if rule.get("type") == "tabs":
            content = _apply_tabs(content)

    return content


def process_dir(src_dir: Path, src_root: Path = None, swap_list_path: Path = None) -> int:
    """Process all MD files using rules from swap_list.yml.
    Returns count of modified files.
    """
    if src_root is None:
        src_root = src_dir
    if swap_list_path is None:
        swap_list_path = _SWAP_LIST

    rules = _load_swap_list(swap_list_path)

    count = 0
    for md_file in sorted(src_dir.rglob("*.md")):
        if ".vuepress" in str(md_file):
            continue
        content = md_file.read_text(encoding="utf-8")
        new_content = _apply_all_rules(content, rules, md_file, src_root)
        if new_content != content:
            md_file.write_text(new_content, encoding="utf-8")
            count += 1
    return count
