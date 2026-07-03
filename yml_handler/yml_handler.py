import re
import yaml
from pathlib import Path

from data_models.umda_data_yml import UMDAData
from data_models.umda_config import UMDAConfig, AdapterConfig

# UMDA_ROOT is the directory where umda package lives
UMDA_ROOT = Path(__file__).parent.parent

# Matches top-level: include: ./some/file.yml
_INCLUDE_RE = re.compile(r'^include:\s*(.+)$', re.MULTILINE)


class YMLHandler:
    def __init__(self, umda_yml_file: Path = Path("./umda.yml")):
        self.umda_yml_file = Path(umda_yml_file)
        self.base_dir = self.umda_yml_file.parent

        raw = self._load_raw(self.umda_yml_file)

        self.config = UMDAConfig(**raw.get("config", {}))

        # Parse adapters — key in yml is "adapers" (legacy typo kept)
        raw_adapters: dict = raw.get("adapers", {})
        self.adapters: dict[str, AdapterConfig] = {}
        for name, cfg in raw_adapters.items():
            adapter_cfg = AdapterConfig(**cfg)
            # Auto-resolve swap_list from adapter directory if not set
            adapter_dir = UMDA_ROOT / "adapters" / name
            if adapter_cfg.swap_list is None:
                auto = adapter_dir / "swap_list.yml"
                if auto.exists():
                    adapter_cfg.swap_list = auto
            else:
                sl = Path(adapter_cfg.swap_list)
                if not sl.is_absolute():
                    adapter_cfg.swap_list = (adapter_dir / sl).resolve()
            self.adapters[name] = adapter_cfg

        # UMDAData is now populated per-adapter from sections (see main.py)
        self.data = UMDAData()

    def _load_with_includes(self, file_path: Path) -> dict:
        """Load a yaml file, resolving all include: directives recursively."""
        file_path = Path(file_path)
        base_dir = file_path.parent
        text = file_path.read_text(encoding="utf-8")

        # Collect all include paths (preserving order) before yaml parsing
        include_paths = [
            (base_dir / m.group(1).strip()).resolve()
            for m in _INCLUDE_RE.finditer(text)
        ]

        # Strip include lines so yaml can parse the rest cleanly
        clean_text = _INCLUDE_RE.sub("", text)
        result: dict = yaml.safe_load(clean_text) or {}

        # Deep-merge all included files first, then overlay with result
        # (file's own keys win over included keys)
        included: dict = {}
        for inc_path in include_paths:
            if not inc_path.exists():
                print(f"WARN: include not found: {inc_path}")
                continue
            included.update(self._load_with_includes(inc_path))

        # included is base, result overlays it
        merged = _deep_merge(included, result)
        return merged

    def _load_raw(self, file_path: Path) -> dict:
        with open(file_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Override wins on conflict."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result
