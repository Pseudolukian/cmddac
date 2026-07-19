import re
import sys
import yaml
from pathlib import Path

from logging_utils import error

_DEFAULT_SWAP_LIST = Path(__file__).parent / "swap_list.yml"


def _parse_pattern(from_str: str) -> re.Pattern:
    """Parse r'...' or r"..." string from yaml into compiled re.Pattern."""
    s = from_str.strip()
    if s.startswith("r'"):
        s = s[2:]
    elif s.startswith('r"'):
        s = s[2:]
    if s.endswith("'") or s.endswith('"'):
        s = s[:-1]
    return re.compile(s, re.MULTILINE)


class MKdocsAdapter:
    """
    Adapts a processed UMDA bundle for MkDocs rendering.
    Reads swap_list.yml and applies regex substitutions to all .md files
    in doc_output.
    """

    def __init__(self, doc_output: Path, swap_list_path: Path = _DEFAULT_SWAP_LIST, src_root: Path = None, **kwargs):
        self.doc_output = Path(doc_output)
        self.swap_list_path = Path(swap_list_path)
        self.src_root = Path(src_root) if src_root else self.doc_output
        self.swap_rules: dict = self._load_swap_list()

    def _load_swap_list(self) -> dict:
        with open(self.swap_list_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def run(self):
        print(f"[MKdocsAdapter] processing: {self.doc_output}")
        for md_file in sorted(self.doc_output.rglob("*.md")):
            self._process_file(md_file)

    def _process_file(self, md_file: Path):
        content = md_file.read_text(encoding="utf-8")
        new_content = self._apply_swaps(content)
        if new_content != content:
            md_file.write_text(new_content, encoding="utf-8")
            rel = md_file.relative_to(self.doc_output)
            print(f"[MKdocsAdapter] updated: {rel}")

    def _apply_swaps(self, content: str) -> str:
        # Process includes FIRST — before any other swaps
        content = self._apply_includes(content)

        for rule_name, rule in self.swap_rules.items():
            convert = rule.get("convert", {})
            from_raw = convert.get("from", "")
            to_raw = convert.get("to", "")

            if rule_name == "tabs":
                content = self._apply_tabs(content)
            elif rule_name == "include":
                pass  # already handled above
            else:
                if not from_raw or not to_raw:
                    continue
                pattern = _parse_pattern(from_raw)
                to_str = to_raw.strip() if isinstance(to_raw, str) else str(to_raw)
                content = pattern.sub(to_str, content)

        return content

    def _apply_includes(self, content: str) -> str:
        """Replace ➡️ (path/to/file.md) with actual file content (inline include)."""
        pattern = re.compile(r'^➡️\s*\((.+?)\)\s*$', re.MULTILINE)

        def replacer(m):
            file_path = m.group(1).strip()
            # Resolve relative to src_root (original doc source)
            target = self.src_root / file_path
            if not target.exists():
                # Try case-insensitive match
                parent = target.parent
                if parent.exists():
                    for f in parent.iterdir():
                        if f.name.lower() == target.name.lower():
                            target = f
                            break
            if target.exists():
                included = target.read_text(encoding='utf-8').rstrip()
                return included
            else:
                # ponytail: mkdocs _apply_includes не имеет md_file в области видимости —
                # annotation без file/line, виден в Checks как сообщение
                error(f"include not found: {file_path}")
                return m.group(0)

        return pattern.sub(replacer, content)

    def _apply_tabs(self, content: str) -> str:
        lines = content.split("\n")
        result: list[str] = []
        i = 0
        while i < len(lines):
            if lines[i].strip() == "🗂️":
                i += 1
                block: list[str] = []
                while i < len(lines):
                    line = lines[i]
                    if line == "":
                        block.append(line)
                        i += 1
                    elif line[0].isspace() or re.match(r"^\d+\.", line):
                        block.append(line)
                        i += 1
                    else:
                        break
                result.extend(self._convert_tabs_block(block))
            else:
                result.append(lines[i])
                i += 1
        return "\n".join(result)

    def _convert_tabs_block(self, block_lines: list[str]) -> list[str]:
        tabs: list[tuple[str, list[str]]] = []
        current_name: str | None = None
        current_body: list[str] = []

        for line in block_lines:
            m = re.match(r"^\d+\.\s+(.+)", line)
            if m:
                if current_name is not None:
                    tabs.append((current_name, current_body))
                current_name = m.group(1).strip()
                current_body = []
            else:
                if current_name is not None:
                    current_body.append(line)

        if current_name is not None:
            tabs.append((current_name, current_body))

        result: list[str] = []
        for tab_name, body in tabs:
            result.append(f'=== "{tab_name}"')
            for line in body:
                result.append(line)
            if not result[-1] == "":
                result.append("")

        return result
