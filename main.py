#!/usr/bin/env python3
"""
UMDA CLI entry point.

Usage:
    umda <adapter_name> [build]

Searches for umda.yml upward from the current directory.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from yml_handler.yml_handler import YMLHandler, UMDA_ROOT
from md_handler.md_handler import MDHandle
from psd_handler.psd_handler import PSDHandler
from config_handler.config_handler import MkDocsConfigHandler, _load_with_includes
from data_models.umda_data_yml import UMDAData


def usage():
    print("Usage: umda <adapter_name> [build]")
    print("Example: umda mkdocs build")
    sys.exit(1)


def find_umda_yml(start: Path) -> Path:
    current = start.resolve()
    while True:
        candidate = current / "umda.yml"
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            print(f"ERROR: umda.yml not found in '{start}' or any parent directory.")
            sys.exit(1)
        current = parent


def ensure_dirs(adapter_cfg):
    Path(adapter_cfg.doc_output).mkdir(parents=True, exist_ok=True)
    Path(adapter_cfg.media.media_storage_output).mkdir(parents=True, exist_ok=True)


def build_umda_data(sections: list[Path]) -> UMDAData:
    """Merge all adapter sections (with include: support) into UMDAData."""
    merged: dict = {}
    paths = sections.values() if isinstance(sections, dict) else sections
    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        merged.update(_load_with_includes(p))
    return UMDAData(**merged)


def build(adapter_name: str):
    umda_yml = find_umda_yml(Path.cwd())
    docs_dir = umda_yml.parent

    yml_handler = YMLHandler(umda_yml_file=umda_yml)

    if adapter_name not in yml_handler.adapters:
        available = list(yml_handler.adapters.keys())
        print(f"ERROR: adapter '{adapter_name}' not found in umda.yml. Available: {available}")
        sys.exit(1)

    adapter_cfg = yml_handler.adapters[adapter_name]
    ensure_dirs(adapter_cfg)

    print(f"[UMDA] Building '{adapter_name}' adapter")
    print(f"  umda.yml:             {umda_yml}")
    print(f"  doc_input:            {yml_handler.config.doc_input}")
    print(f"  doc_output:           {adapter_cfg.doc_output}")
    print(f"  media_storage_output: {adapter_cfg.media.media_storage_output}")

    # 1. Config handler — patch and save mkdocs config (skipped if mkdocs_path not set)
    if adapter_cfg.mkdocs_path is not None and adapter_cfg.config is not None:
        config_handler = MkDocsConfigHandler(
            mkdocs_path=adapter_cfg.mkdocs_path,
            cfg=adapter_cfg.config,
        )
        config_handler.run()
        data = build_umda_data(adapter_cfg.config.sections)
    else:
        # Load vars if available (e.g. for vuepress_hope)
        if adapter_cfg.vars and Path(adapter_cfg.vars).exists():
            data = build_umda_data({"vars": adapter_cfg.vars})
        else:
            data = build_umda_data({})

    # 3. PSDHandler
    psd_handler = PSDHandler(
        output_dir=adapter_cfg.media.media_storage_output,
        image_ext=adapter_cfg.media.image_extantion,
    )

    # 4. MDHandle — process MD files, convert images
    md_handle = MDHandle(
        data=data,
        docs_dir=docs_dir,
        adapter_cfg=adapter_cfg,
        psd_handler=psd_handler,
    )
    md_handle.run()
    psd_handler.terminate()

    # 4b. VuePress vars injection (if vars path is set)
    if adapter_cfg.vars is not None:
        from adapters.vuepress_hope.vars_injector import VuepressVarsInjector
        vars_data = _load_with_includes(adapter_cfg.vars)
        injector = VuepressVarsInjector(vars_data)
        count = injector.process_dir(Path(adapter_cfg.doc_output))
        print(f"[UMDA] VuePress vars injected into {count} file(s)")

    # 5. Adapter-specific post-processing (swap_list)
    _run_adapter(adapter_name, adapter_cfg, src_root=docs_dir)

    print(f"[UMDA] Done. Bundle ready at: {adapter_cfg.doc_output}")


def _run_adapter(adapter_name: str, adapter_cfg, src_root=None):
    adapter_dir = UMDA_ROOT / "adapters" / adapter_name
    adapter_main = adapter_dir / "main.py"

    if not adapter_main.exists():
        print(f"WARN: no adapter module found at {adapter_main}, skipping post-processing")
        return

    import importlib.util
    spec = importlib.util.spec_from_file_location(f"adapters.{adapter_name}.main", adapter_main)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class_name = adapter_name.capitalize() + "Adapter"
    adapter_class = getattr(mod, "MKdocsAdapter", None) or getattr(mod, class_name, None)

    if adapter_class is None:
        print(f"WARN: adapter class not found in {adapter_main}, skipping")
        return

    swap_list_path = adapter_cfg.swap_list or (adapter_dir / "swap_list.yml")

    # Build kwargs for adapter constructor
    kwargs = {
        "doc_output": adapter_cfg.doc_output,
        "swap_list_path": swap_list_path,
    }
    # Pass nav_path for vuepress_hope (from config.sections.nav or standalone nav field)
    if adapter_cfg.nav:
        kwargs["nav_path"] = adapter_cfg.nav
    elif adapter_cfg.config and adapter_cfg.config.sections:
        nav_path = adapter_cfg.config.sections.get("nav")
        if nav_path:
            kwargs["nav_path"] = nav_path
    if adapter_cfg.vuepress_path:
        kwargs["vuepress_path"] = adapter_cfg.vuepress_path
    if hasattr(adapter_cfg, 'media') and adapter_cfg.media:
        kwargs["media_base_url"] = adapter_cfg.media.media_base_url
    if src_root:
        kwargs["src_root"] = str(src_root)
    # Pass base path for VuePress
    if hasattr(adapter_cfg, 'base') and adapter_cfg.base:
        kwargs["base"] = adapter_cfg.base

    instance = adapter_class(**kwargs)
    instance.run()


def main():
    args = sys.argv[1:]
    if not args:
        usage()

    adapter_name = args[0]
    subcommand = args[1] if len(args) > 1 else "build"

    if subcommand not in ("build",):
        print(f"ERROR: unknown subcommand '{subcommand}'. Supported: build")
        sys.exit(1)

    build(adapter_name)


if __name__ == "__main__":
    main()
