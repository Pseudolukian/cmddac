#!/usr/bin/env python3
"""
VuepressHope adapter post-processing step.
Called by UMDA after doc_output is populated.

Reads nav.yaml, generates navbar + sidebar, renders config.ts via Jinja2
template, and writes to vuepress_path/config.ts.
"""
from __future__ import annotations

import json
import yaml
from pathlib import Path
from typing import Any

from logging_utils import error

UMDA_ROOT = Path(__file__).parent.parent.parent


def _to_link(path: str) -> str:
    """Convert 'section/index.md' -> '/section/', 'page.md' -> '/page.html'"""
    p = path.strip("/")
    if p == "index.md":
        return "/"
    if p.endswith("/index.md"):
        return "/" + p[: -len("/index.md")] + "/"
    if p.endswith(".md"):
        return "/" + p[:-3] + ".html"
    return "/" + p + "/"


def _first_link(val: Any) -> str | None:
    """Return the first file link found in a nav value."""
    if isinstance(val, str):
        return _to_link(val)
    if isinstance(val, list):
        for item in val:
            if isinstance(item, str):
                return _to_link(item)
            if isinstance(item, dict):
                for v in item.values():
                    r = _first_link(v)
                    if r:
                        return r
    if isinstance(val, dict):
        for v in val.values():
            r = _first_link(v)
            if r:
                return r
    return None


def _build_sidebar_children(items: Any) -> list[dict]:
    """Recursively build sidebar children from nav items."""
    result = []
    if isinstance(items, str):
        # Leaf node — a bare path, no children to produce
        return []

    if isinstance(items, list):
        for item in items:
            if isinstance(item, str):
                # Bare path like "index.md" — skip section indexes
                link = _to_link(item)
                if link.endswith("/"):
                    continue
                result.append({"text": Path(item).stem, "link": link})
            elif isinstance(item, dict):
                for title, val in item.items():
                    entry: dict[str, Any] = {"text": title}
                    link = _first_link(val)
                    if link:
                        entry["link"] = link
                    children = _build_sidebar_children(val)
                    if children:
                        entry["children"] = children
                    result.append(entry)

    elif isinstance(items, dict):
        for title, val in items.items():
            entry = {"text": title}
            link = _first_link(val)
            if link:
                entry["link"] = link
            children = _build_sidebar_children(val)
            if children:
                entry["children"] = children
            result.append(entry)

    return result


def build_nav(nav_path: Path) -> tuple[list[dict], dict[str, list]]:
    """
    Parse nav.yaml.
    Returns (navbar, sidebar).
    - navbar: top-level items [{text, link}]
    - sidebar: { "/section/": [children...] }
    """
    nav = yaml.safe_load(nav_path.read_text(encoding="utf-8"))

    navbar = []
    sidebar = {}

    for section_title, section_val in nav.items():
        link = _first_link(section_val)
        navbar.append({"text": section_title, "link": link or "/"})

        # Sidebar key: /admins/, /all/, etc.
        if link and link != "/":
            parts = link.strip("/").split("/")
            sidebar_key = "/" + parts[0] + "/"
        else:
            sidebar_key = "/"

        children = _build_sidebar_children(section_val)
        if children:
            sidebar[sidebar_key] = children

    return navbar, sidebar


def render_config(template_path: Path, navbar: list, sidebar: dict, base: str = "/") -> str:
    """Render config.j2 template with navbar/sidebar/base data."""
    template = template_path.read_text(encoding="utf-8")

    navbar_json = json.dumps(navbar, ensure_ascii=False, indent=8)
    sidebar_json = json.dumps(sidebar, ensure_ascii=False, indent=8)

    result = template.replace("{{NAVBAR}}", navbar_json)
    result = result.replace("{{SIDEBAR}}", sidebar_json)
    result = result.replace("{{BASE}}", base)
    return result


class Vuepress_hopeAdapter:
    """Post-processor for vuepress_hope adapter."""

    def __init__(self, doc_output, swap_list_path=None, nav_path=None, vuepress_path=None, media_base_url=None, src_root=None, base=None, **kwargs):
        self.doc_output = Path(doc_output)
        self.swap_list_path = swap_list_path
        self.nav_path = nav_path
        self.vuepress_path = vuepress_path
        self.media_base_url = media_base_url or "/media"
        self.src_root = Path(src_root) if src_root else self.doc_output
        self.base = base or "/"

    def run(self):
        if not self.nav_path or not self.vuepress_path:
            print("[VuepressHope] nav_path or vuepress_path not set, skipping config generation")
            return

        nav_path = Path(self.nav_path)
        vuepress_path = Path(self.vuepress_path)
        template_path = Path(__file__).parent / "templates" / "config.j2"

        if not nav_path.exists():
            error(f"nav.yaml not found: {nav_path}", file=str(nav_path))
            return

        if not template_path.exists():
            error(f"template not found: {template_path}", file=str(template_path))
            return

        # Copy UMDA output docs into VuePress src/ directory
        # vuepress_path = .../src/.vuepress, so src_dir = vuepress_path.parent
        import shutil
        src_dir = vuepress_path.parent
        doc_output = self.doc_output

        print(f"[VuepressHope] copying docs: {doc_output} -> {src_dir}")
        skip_dirs = {".vuepress", "_templates", "guide"}  # skip VuePress internals + mkdocs-only templates
        for item in sorted(doc_output.iterdir()):
            if item.name in skip_dirs:
                continue
            dst = src_dir / item.name
            if item.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(item, dst)
            else:
                shutil.copy2(item, dst)

        # Count copied
        copied = [p for p in doc_output.iterdir() if p.name not in skip_dirs]
        print(f"[VuepressHope] copied {len(copied)} items to {src_dir}")

        # Convert markdown image links /media/... to raw HTML <img> tags
        # VuePress/Vite tries to resolve ![](/media/...) as module imports, which fails.
        # Raw <img> tags are passed through as-is.
        import re
        _ABS_IMG_RE = re.compile(r'!\[([^\]]*)\]\((/media/[^)]+)\)')
        img_fix_count = 0
        for md_file in sorted(src_dir.rglob("*.md")):
            if '.vuepress' in str(md_file):
                continue
            content = md_file.read_text(encoding="utf-8")
            # /media/ links should already be absolute HTTP URLs from md_handler
            # (media_base_url in umda.yml is http://...)
            # But if any relative /media/ remain, skip — they won't resolve in VuePress
            if '/media/' not in content or 'http' in content.split('/media/')[0][-10:]:
                continue
            # Safety: convert any straggler /media/ markdown images to use media_base_url
            new_content = _ABS_IMG_RE.sub(
                lambda m: f'![{m.group(1)}]({self.media_base_url}{m.group(2)})',
                content
            )
            if new_content != content:
                img_fix_count += 1
                md_file.write_text(new_content, encoding="utf-8")
        if img_fix_count:
            print(f"[VuepressHope] converted {img_fix_count} /media/ image links")

        # Convert mkdocs admonition syntax to VuePress Hope hint containers
        from adapters.vuepress_hope.syntax_converter import process_dir as convert_syntax
        swap_path = self.swap_list_path or (Path(__file__).parent / "swap_list.yml")
        syntax_count = convert_syntax(src_dir, src_root=self.src_root, swap_list_path=swap_path)
        if syntax_count:
            print(f"[VuepressHope] converted admonitions in {syntax_count} file(s)")

        # Build navbar + sidebar from nav.yaml
        navbar, sidebar = build_nav(nav_path)

        print(f"[VuepressHope] navbar: {len(navbar)} items")
        for item in navbar:
            print(f"  {item['text']} -> {item['link']}")

        print(f"[VuepressHope] sidebar: {len(sidebar)} sections")
        for key, items in sidebar.items():
            print(f"  {key}: {len(items)} item(s)")

        # Render config.ts
        config_ts = render_config(template_path, navbar, sidebar, base=self.base)

        # Write to vuepress_path
        vuepress_path.mkdir(parents=True, exist_ok=True)
        out_file = vuepress_path / "config.ts"
        out_file.write_text(config_ts, encoding="utf-8")
        print(f"[VuepressHope] config.ts written to {out_file}")
