import re
import shutil
import yaml
from pathlib import Path
from PIL import Image

from data_models.umda_data_yml import UMDAData
from data_models.umda_config import AdapterConfig
from data_models.psd_config import PSDConfig
from psd_handler.psd_handler import PSDHandler

# Matches: ![alt]({{ media.path.var }})
_PSD_LINK_RE = re.compile(r'(!\[(.+?)\]\(\{\{\s*([\w.]+)\s*\}\}\))')

# Matches: ![alt](path/to/image.ext) — local image, not http.
# Alt text may contain [] (e.g. PSD layer directives like Focuses=["A"]).
# Path may start with {{ var.path }} (e.g. {{ media.screenshots.diagram }}/foo.png);
# such vars are resolved in img_replacer from global UMDAData.
# Note: PSD-style links ![alt]({{ var }}) (no extension) are matched earlier by
# _PSD_LINK_RE, so allowing {{ here cannot shadow them.
_IMG_LINK_RE = re.compile(r'(!\[([^\]]*(?:\[[^\]]*\][^\]]*)*)\]\((?!https?://)([^)]+\.(?:png|jpg|jpeg|gif|webp|svg))\))', re.IGNORECASE)

# Parses alt: "Base;Focuses=[A,B];Frames=[C,D]"
_ARG_RE = re.compile(r'(\w+)=\[([^\]]*)\]')

# Matches include marker: ➡️ (path/to/file.md)
_INCLUDE_RE = re.compile(r'^➡️\s*\((.+?)\)\s*$', re.MULTILINE)

# Matches {{ var.path }} inside strings (e.g. image URLs) for frontmatter resolution
_FM_VAR_RE = re.compile(r'\{\{\s*([\w.]+)\s*\}\}')


def _parse_frontmatter(md_file: Path) -> dict:
    """Extract YAML frontmatter from a markdown file. Returns {} if none."""
    try:
        content = md_file.read_text(encoding="utf-8")
    except Exception:
        return {}
    if not content.startswith("---"):
        return {}
    try:
        end = content.index("---", 3)
    except ValueError:
        return {}
    fm_text = content[3:end].strip()
    try:
        return yaml.safe_load(fm_text) or {}
    except Exception:
        return {}


def _resolve_from_dict(data: dict, dotted_key: str):
    """Resolve 'a.b.c' from nested dict. Returns None if not found."""
    node = data
    for k in dotted_key.split("."):
        if isinstance(node, dict) and k in node:
            node = node[k]
        else:
            return None
    return node


def _resolve_fm_vars(text: str, fm: dict) -> str:
    """Replace {{ var.path }} in text with values from frontmatter dict.
    Unresolved vars are left as-is."""
    def repl(m: re.Match) -> str:
        val = _resolve_from_dict(fm, m.group(1))
        return str(val) if val is not None else m.group(0)
    return _FM_VAR_RE.sub(repl, text)


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
        self.local_media_root = self.media_storage_output
        self.image_ext = adapter_cfg.media.image_extantion.lower().lstrip(".")
        self.media_base_url = adapter_cfg.media.media_base_url.rstrip("/")
        self.psd_handler = psd_handler

        self.local_media_root.mkdir(parents=True, exist_ok=True)

    def run(self):
        # .meta.yml support removed — metadata now lives in frontmatter

        self.copy_media_dir()

        for md_file in sorted(self.docs_dir.rglob("*.md")):
            self.md_loader(md_file)

    def copy_media_dir(self):
        """Copy docs_dir/media/ -> local_media_root with webp conversion.

        Mirrors the media directory into the media storage output so that
        absolute /media/... links in MD resolve at render time. Images are
        converted to the configured image_ext (webp by default); non-image
        files are copied as-is. Frontmatter vars in links (e.g. {{ alias }})
        are resolved in img_replacer from the file's frontmatter.
        """
        src_root = self.docs_dir / "media"
        if not src_root.exists() or not src_root.is_dir():
            return

        converted = 0
        img_exts = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff"}
        for src in sorted(src_root.rglob("*")):
            if not src.is_file():
                continue
            # ponytail: rel относительно media/, не docs_dir — иначе в local_media_root
            # (который уже = public/media) получается двойное /media/media/...
            rel = src.relative_to(src_root)
            src_ext = src.suffix.lower().lstrip(".")

            if src_ext in img_exts and src_ext != "svg":
                dst = (self.local_media_root / rel).with_suffix(f".{self.image_ext}")
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src_ext == self.image_ext:
                    shutil.copy2(src, dst)
                else:
                    with Image.open(src) as img:
                        mode = "RGBA" if self.image_ext == "webp" else "RGB"
                        img.convert(mode).save(dst, format=self.image_ext)
            else:
                dst = self.local_media_root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            converted += 1

        print(f"[MDHandle] processed {converted} media file(s): {src_root} -> {self.local_media_root}")

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
                    rel_path = out.relative_to(self.media_storage_output)
                    return f"![{alt}]({self._media_link(rel_path)})"
                elif out.is_relative_to(self.docs_dir):
                    return f"![{alt}]({out.relative_to(self.docs_dir)})"
                return f"![{alt}]({out})"

            content = _PSD_LINK_RE.sub(psd_replacer, content)

            def img_replacer(m: re.Match) -> str:
                nonlocal count
                full_match = m.group(1)
                alt = m.group(2)
                img_path_str = m.group(3).strip()

                # Resolve {{ var.path }} in image paths. Primary source is the
                # global UMDAData (vars/media.yml etc. with proper nested
                # structure); frontmatter of the current file is a fallback
                # (tried both nested and as a flat "a.b.c" key, since YAML
                # parses `a.b.c: value` as a single string key, not nested).
                # After resolution the path becomes /media/... or media/...
                # and is served from the media storage output, populated in
                # bulk by copy_media_dir(). The extension is rewritten to
                # image_ext (webp) and media_base_url is prepended.
                if "{{" in img_path_str:
                    fm = _parse_frontmatter(md_file)

                    def _resolve_var(var_key: str):
                        val = self.data.resolve(var_key)
                        if val is None and fm:
                            val = _resolve_from_dict(fm, var_key)
                        if val is None and fm:
                            val = fm.get(var_key)
                        return val

                    def _var_repl(mm: re.Match) -> str:
                        resolved = _resolve_var(mm.group(1))
                        return str(resolved) if resolved is not None else mm.group(0)

                    img_path_str = _FM_VAR_RE.sub(_var_repl, img_path_str)

                if img_path_str.startswith("/media/") or img_path_str.startswith("media/"):
                    # ponytail: убираем media/ префикс — он уже в media_base_url,
                    # иначе двойное /media/media/ в итоговом URL
                    stripped = img_path_str.lstrip("/")
                    if stripped.startswith("media/"):
                        stripped = stripped[len("media/"):]
                    rel_path = Path(stripped).with_suffix(f".{self.image_ext}")
                    count += 1
                    return f"![{alt}]({self._media_link(rel_path)})"

                src = (md_file.parent / img_path_str).resolve()
                if not src.exists():
                    print(f"  WARN: image not found '{src}' — skipping")
                    return full_match

                try:
                    rel_to_docs = src.relative_to(self.docs_dir)
                except ValueError:
                    rel_to_docs = Path(src.name)

                # ponytail: если src в docs/media/, убираем media/ префикс —
                # local_media_root уже = public/media, иначе двойное /media/media/
                if rel_to_docs.parts and rel_to_docs.parts[0] == "media":
                    rel_to_docs = rel_to_docs.relative_to(Path("media"))

                dst = (self.local_media_root / rel_to_docs).with_suffix(f".{self.image_ext}")
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
                    rel_path = dst.relative_to(self.media_storage_output)
                    return f"![{alt}]({self._media_link(rel_path)})"
                return f"![{alt}]({dst})"

            content = _IMG_LINK_RE.sub(img_replacer, content)
            return content, count
        finally:
            include_stack.remove(current_file)

    def _media_link(self, rel_path: Path) -> str:
        rel_posix = rel_path.as_posix().lstrip("/")
        if self.media_base_url:
            return f"{self.media_base_url}/{rel_posix}"
        return str(self.media_storage_output / rel_path)
