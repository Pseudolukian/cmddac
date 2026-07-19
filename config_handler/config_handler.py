"""
MkDocs config handler.

Reads {mkdocs_path}/mkdocs.yml, applies options patches (dot-notation keys)
and merges sections from external yaml files (with include: support),
then saves the result as {mkdocs_path}/{output_config_name}.yml.
"""
from __future__ import annotations

import re
import yaml
from pathlib import Path
from typing import Any
from ruamel.yaml import YAML as RuamelYAML

from data_models.umda_config import AdapterConfigSection
from logging_utils import error

# Matches top-level: include: ./some/file.yml (multiple allowed)
_INCLUDE_RE = re.compile(r'^include:\s*(.+)$', re.MULTILINE)

# Custom loader that passes through !!python/name: tags as plain strings
class _PassthroughLoader(yaml.SafeLoader):
    pass

def _python_name_constructor(loader, tag_suffix, node):
    return loader.construct_scalar(node)

_PassthroughLoader.add_multi_constructor(
    "tag:yaml.org,2002:python/",
    _python_name_constructor,
)


def _set_nested(d: dict, dot_key: str, value: Any) -> None:
    """Set a value in a nested dict using dot-notation key."""
    keys = dot_key.split(".")
    node = d
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = value


def _deep_merge(base: Any, incoming: Any) -> Any:
    """Recursively merge incoming into base. Incoming values win on conflict."""
    if isinstance(base, dict) and isinstance(incoming, dict):
        result = dict(base)
        for k, v in incoming.items():
            result[k] = _deep_merge(result.get(k), v)
        return result
    return incoming


def _dict_to_nav_list(d: Any) -> Any:
    """Convert dict-style nav to MkDocs list-style nav recursively."""
    if isinstance(d, str):
        return d
    if isinstance(d, list):
        result = []
        for item in d:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                for k, v in item.items():
                    result.append({k: _dict_to_nav_list(v)})
            else:
                result.append(item)
        return result
    if isinstance(d, dict):
        result = []
        for k, v in d.items():
            result.append({k: _dict_to_nav_list(v)})
        return result
    return d


def _load_with_includes(file_path: Path) -> dict:
    """Load a yaml file resolving all include: directives recursively.

    include: directives may appear multiple times at any level — they are
    pre-processed as text before yaml parsing.
    """
    file_path = Path(file_path).resolve()
    base_dir = file_path.parent
    text = file_path.read_text(encoding="utf-8")

    # Collect all include paths in order
    include_paths = [
        (base_dir / m.group(1).strip()).resolve()
        for m in _INCLUDE_RE.finditer(text)
    ]

    # Strip include lines so yaml can parse the rest
    clean_text = _INCLUDE_RE.sub("", text)
    own_data: dict = yaml.safe_load(clean_text) or {}

    # Load included files first, then overlay own data
    included: dict = {}
    for inc_path in include_paths:
        if not inc_path.exists():
            error(f"include not found: {inc_path}", file=str(file_path))
            continue
        included = _deep_merge(included, _load_with_includes(inc_path))

    return _deep_merge(included, own_data)


class MkDocsConfigHandler:
    def __init__(self, mkdocs_path: Path, cfg: AdapterConfigSection):
        self.mkdocs_path = Path(mkdocs_path)
        self.cfg = cfg
        self.src_config = self.mkdocs_path / "mkdocs.yml"

    def run(self) -> Path:
        """Build patched config and save. Returns path to the new config file."""
        ry = RuamelYAML()
        ry.preserve_quotes = True
        ry.width = 4096

        with open(self.src_config, "r", encoding="utf-8") as f:
            data = ry.load(f)

        # 1. Patch options (dot-notation keys)
        for dot_key, value in self.cfg.options.items():
            _set_nested(data, dot_key, value)
            print(f"[MkDocsConfig] set {dot_key} = {value!r}")

        # 2. Merge sections from external yaml files (with include: support)
        for section_name, section_path in self.cfg.sections.items():
            p = Path(section_path)
            if not p.exists():
                error(f"section file not found: {p}", file=str(self.src_config))
                continue

            section_data = _load_with_includes(p)

            if not section_data:
                error(f"empty section file: {p}", file=str(self.src_config))
                continue

            # Unwrap if file has {section_name: ...} wrapper
            if isinstance(section_data, dict) and section_name in section_data and len(section_data) == 1:
                section_data = section_data[section_name]

            # nav must be a list in MkDocs
            if section_name == "nav" and isinstance(section_data, dict):
                section_data = _dict_to_nav_list(section_data)

            if section_name in data:
                data[section_name] = _deep_merge(data[section_name], section_data)
            else:
                data[section_name] = section_data

            print(f"[MkDocsConfig] merged section '{section_name}' from {p.name}")

        # 3. Save result
        out_path = self.mkdocs_path / f"{self.cfg.output_config_name}.yml"
        with open(out_path, "w", encoding="utf-8") as f:
            ry.dump(data, f)

        print(f"[MkDocsConfig] saved: {out_path}")
        return out_path
