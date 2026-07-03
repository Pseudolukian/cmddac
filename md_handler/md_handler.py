import re
import shutil
from pathlib import Path
from PIL import Image

from data_models.umda_data_yml import UMDAData
from data_models.umda_config import AdapterConfig
from data_models.psd_config import PSDConfig
from psd_handler.psd_handler import PSDHandler

# Matches: ![alt]({{ media.path.var }})
_PSD_LINK_RE = re.compile(r'(!\[(.+?)\]\(\{\{\s*([\w.]+)\s*\}\}\))')

# Matches: ![alt](path/to/image.ext) — local image, not a {{ }} var, not http
# Alt text may contain [] (e.g. PSD layer directives like Focuses=["A"])
_IMG_LINK_RE = re.compile(r'(!\[([^\]]*(?:\[[^\]]*\][^\]]*)*)\]\((?!https?://)(?!\{\{)([^)]+\.(?:png|jpg|jpeg|gif|webp|svg))\))', re.IGNORECASE)

# Parses alt: "Base;Focuses=[A,B];Frames=[C,D]"
_ARG_RE = re.compile(r'(\w+)=\[([^\]]*)\]')

# Matches include marker: ➡️ (path/to/file.md)
_INCLUDE_RE = re.compile(r'^➡️\s*\((.+?)\)\s*$', re.MULTILINE)


class MDHandle:
    def __init__(
        self,
        data: UMDAData,
        docs_dir: Path,
        adapter_cfg: AdapterConfig,
        psd_handler: PSDHandler,
    ):
        self.data = data
        self.docs_dir = Path(docs_dir)
        self.adapter_cfg = adapter_cfg
        self.doc_output = Path(adapter_cfg.doc_output)
        self.media_storage_output = Path(adapter_cfg.media.media_storage_output)
        self.image_ext = adapter_cfg.media.image_extantion.lower().lstrip(".")
        self.media_base_url = adapter_cfg.media.media_base_url.rstrip("/")
        self.psd_handler = psd_handler

    def run(self):
        # .meta.yml support removed — metadata now lives in frontmatter

        for md_file in sorted(self.docs_dir.rglob("*.md")):
            self.md_loader(md_file)

    def md_loader(self, md_file: Path):
        content = md_file.read_text(encoding="utf-8")
        new_content, count = self.md_process(content, md_file)

        rel = md_file.relative_to(self.docs_dir)
        out_file = self.doc_output / rel
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(new_content, encoding="utf-8")

        if count:
            print(f"[{rel}] updated {count} image(s)")

    def _resolve_include_target(self, include_path: str, md_file: Path) -> Path | None:
        rel = Path(include_path)

        candidates: list[Path] = []
        if rel.is_absolute():
            candidates.append(rel)
        elif include_path.startswith("./") or include_path.startswith("../"):
            candidates.extend([
                (md_file.parent / rel).resolve(),
                (self.docs_dir / rel).resolve(),
            ])
        else:
            candidates.extend([
                (self.docs_dir / rel).resolve(),
                (md_file.parent / rel).resolve(),
            ])

        for target in candidates:
            if target.exists():
                return target
            parent = target.parent
            if parent.exists():
                for f in parent.iterdir():
                    if f.name.lower() == target.name.lower():
                        return f

        return None

    def _expand_includes(self, content: str, md_file: Path, include_stack: set[Path]) -> tuple[str, int]:
        include_count = 0

        def replacer(m: re.Match) -> str:
            nonlocal include_count
            include_path = m.group(1).strip()
            target = self._resolve_include_target(include_path, md_file)
            if not target:
                print(f"  [include] WARNING: not found: {include_path} (in {md_file})")
                return m.group(0)

            target_resolved = target.resolve()
            if target_resolved in include_stack:
                print(f"  [include] WARNING: recursive include skipped: {target} (in {md_file})")
                return m.group(0)

            included_raw = target.read_text(encoding="utf-8")
            included_processed, nested_count = self.md_process(included_raw, target, include_stack)
            include_count += nested_count
            return included_processed.rstrip()

        return _INCLUDE_RE.sub(replacer, content), include_count

    def md_process(self, content: str, md_file: Path, include_stack: set[Path] | None = None) -> tuple[str, int]:
        count = 0

        if include_stack is None:
            include_stack = set()

        current_file = md_file.resolve()
        if current_file in include_stack:
            print(f"  [include] WARNING: recursive include skipped: {md_file}")
            return content, count

        include_stack.add(current_file)
        try:
            content, nested_count = self._expand_includes(content, md_file, include_stack)
            count += nested_count

            def psd_replacer(m: re.Match) -> str:
                nonlocal count
                full_match = m.group(1)
                alt = m.group(2).strip()
                var_path = m.group(3).strip()

                psd_path = self.data.resolve(var_path)
                if not psd_path:
                    print(f"  WARN: cannot resolve '{var_path}' — skipping")
                    return full_match

                parts = alt.split(";")
                base_layer = parts[0].strip()
                kwargs: dict[str, list[str]] = {}
                for part in parts[1:]:
                    arg_m = _ARG_RE.match(part.strip())
                    if arg_m:
                        kwargs[arg_m.group(1)] = [
                            v.strip().strip('"\'') for v in arg_m.group(2).split(",") if v.strip()
                        ]

                try:
                    config = PSDConfig(psd_path=str(psd_path), base_layer=base_layer, **kwargs)
                    out_path = self.psd_handler.render(config)
                except Exception as e:
                    print(f"  ERROR rendering '{alt}': {e}")
                    return full_match

                count += 1
                out = Path(out_path)
                if self.media_base_url and out.is_relative_to(self.media_storage_output):
                    url_path = out.relative_to(self.media_storage_output)
                    return f"![{alt}]({self.media_base_url}/{url_path})"
                elif out.is_relative_to(self.docs_dir):
                    return f"![{alt}]({out.relative_to(self.docs_dir)})"
                return f"![{alt}]({out})"

            content = _PSD_LINK_RE.sub(psd_replacer, content)

            def img_replacer(m: re.Match) -> str:
                nonlocal count
                full_match = m.group(1)
                alt = m.group(2)
                img_path_str = m.group(3).strip()

                src = (md_file.parent / img_path_str).resolve()
                if not src.exists():
                    print(f"  WARN: image not found '{src}' — skipping")
                    return full_match

                try:
                    rel_to_docs = src.relative_to(self.docs_dir)
                except ValueError:
                    rel_to_docs = Path(src.name)

                dst = (self.media_storage_output / rel_to_docs).with_suffix(f".{self.image_ext}")
                dst.parent.mkdir(parents=True, exist_ok=True)

                src_ext = src.suffix.lower().lstrip(".")
                if src_ext == self.image_ext:
                    shutil.copy2(src, dst)
                else:
                    with Image.open(src) as img:
                        mode = "RGBA" if self.image_ext == "webp" else "RGB"
                        img.convert(mode).save(dst, format=self.image_ext)

                count += 1
                if self.media_base_url:
                    url_path = dst.relative_to(self.media_storage_output)
                    return f"![{alt}]({self.media_base_url}/{url_path})"
                return f"![{alt}]({dst})"

            content = _IMG_LINK_RE.sub(img_replacer, content)
            return content, count
        finally:
            include_stack.remove(current_file)
