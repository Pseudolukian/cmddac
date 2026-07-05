"""
VuePress vars injector.

For each MD file:
1. Scan for {{ dotted.var.path }} references (skip media.* — handled by PSD)
2. Resolve values from the merged vars dict
3. Inject used vars into YAML frontmatter
4. Rewrite {{ var }} → {{ $frontmatter.var }} for VuePress Vue-template syntax
"""
from __future__ import annotations

import re
import yaml
from pathlib import Path
from typing import Any

# Matches {{ dotted.path }} but NOT {{ $frontmatter... }} and NOT {{ page.meta... }}
_VAR_RE = re.compile(r'\{\{\s*((?![\$]|page\.meta)[\w]+(?:\.[\w]+)*)\s*\}\}')


def _resolve(data: dict, dotted_key: str) -> Any | None:
    """Resolve 'a.b.c' from nested dict."""
    keys = dotted_key.split(".")
    node = data
    for k in keys:
        if isinstance(node, dict) and k in node:
            node = node[k]
        else:
            return None
    return node


def _set_nested(d: dict, dotted_key: str, value: Any) -> None:
    """Set 'a.b.c' = value in nested dict."""
    keys = dotted_key.split(".")
    node = d
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = value


def _extract_frontmatter(content: str) -> tuple[dict, str]:
    """Split MD into (frontmatter_dict, body). Returns ({}, content) if no frontmatter."""
    if not content.startswith("---"):
        return {}, content
    end = content.index("---", 3)
    fm_text = content[3:end].strip()
    body = content[end + 3:].lstrip("\n")
    fm = yaml.safe_load(fm_text) or {}
    return fm, body


def _rebuild_content(fm: dict, body: str) -> str:
    """Reassemble frontmatter + body."""
    fm_text = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False).rstrip()
    return f"---\n{fm_text}\n---\n\n{body}"


class VuepressVarsInjector:
    """Injects vars into frontmatter and rewrites {{ }} syntax for VuePress."""

    def __init__(self, vars_data: dict, skip_prefixes: tuple[str, ...] = ("media.",)):
        self.vars_data = vars_data
        self.skip_prefixes = skip_prefixes

    def process_file(self, md_path: Path) -> bool:
        """Process one MD file. Returns True if modified."""
        content = md_path.read_text(encoding="utf-8")

        # Find all {{ var.path }} references
        var_refs: set[str] = set()
        for m in _VAR_RE.finditer(content):
            var_key = m.group(1)
            if any(var_key.startswith(p) for p in self.skip_prefixes):
                continue
            var_refs.add(var_key)

        if not var_refs:
            return False

        # Resolve values
        resolved: dict[str, Any] = {}
        for var_key in sorted(var_refs):
            value = _resolve(self.vars_data, var_key)
            if value is not None:
                resolved[var_key] = value
            else:
                print(f"  WARN: cannot resolve var '{var_key}'")

        if not resolved:
            return False

        # Parse frontmatter
        fm, body = _extract_frontmatter(content)

        # Inject vars into frontmatter
        for var_key, value in resolved.items():
            _set_nested(fm, var_key, value)

        # Rewrite {{ var.path }} → {{ $frontmatter.var.path }} in body
        # For VuePress: ALL {{ var }} must go through $frontmatter to avoid Vue errors
        def _rewrite(m: re.Match) -> str:
            var_key = m.group(1)
            if any(var_key.startswith(p) for p in self.skip_prefixes):
                return m.group(0)  # leave media refs untouched
            # Always rewrite to $frontmatter, even if unresolved
            # (unresolved will render as empty/undefined but won't crash Vue)
            return "{{ $frontmatter." + var_key + " }}"

        body = _VAR_RE.sub(_rewrite, body)

        # In headings, replace {{ $frontmatter.var }} with actual values
        # VuePress doesn't interpolate {{ }} in headings (used for TOC/sidebar)
        _FM_VAR_RE = re.compile(r'\{\{\s*\$frontmatter\.((?:[\w]+\.)*[\w]+)\s*\}\}')
        def _resolve_heading(m: re.Match) -> str:
            var_key = m.group(1)
            value = resolved.get(var_key)
            if value is not None and isinstance(value, str):
                return value
            return m.group(0)

        lines = body.split('\n')
        for i, line in enumerate(lines):
            if line.startswith('#'):
                lines[i] = _FM_VAR_RE.sub(_resolve_heading, line)
        body = '\n'.join(lines)

        # Write back
        md_path.write_text(_rebuild_content(fm, body), encoding="utf-8")
        return True

    def process_dir(self, output_dir: Path) -> int:
        """Process all MD files in output_dir. Returns count of modified files."""
        count = 0
        for md_file in sorted(output_dir.rglob("*.md")):
            if self.process_file(md_file):
                rel = md_file.relative_to(output_dir)
                print(f"[VuepressVars] {rel}")
                count += 1
        return count
