from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from PIL import Image, ImageDraw
from psd_tools import PSDImage
from psd_tools.constants import Tag

from data_models.psd_config import PSDConfig


class PSDHandler:
    """Loads PSD files and renders layer compositions to webp."""

    def __init__(self, output_dir: Path, image_ext: str = "webp"):
        self.image_ext = image_ext.lower().lstrip(".")
        self.output_dir = Path(output_dir)
        self._cache: dict[str, PSDImage] = {}
        self._downloaded_tmp: set[Path] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render(self, config: PSDConfig) -> Path:
        """Render layers from a PSDConfig and save as webp."""
        source = config.psd_path.strip()
        cache_key = source
        if cache_key not in self._cache:
            psd_path = self._resolve_psd_source(source)
            self._cache[cache_key] = PSDImage.open(psd_path)
        self.psd = self._cache[cache_key]

        canvas_size = (self.psd.width, self.psd.height)

        group_names_map = {
            "Focuses": set(config.Focuses),
            "Frames": set(config.Frames),
            "Crops": set(config.Crops),
        }

        # Validate base_layer
        top_level_names = [layer.name for layer in self.psd]
        if config.base_layer not in top_level_names:
            raise ValueError(
                f"base_layer '{config.base_layer}' not found in PSD. "
                f"Available: {top_level_names}"
            )

        for upper_dir in self.psd:
            if upper_dir.name != config.base_layer:
                continue

            # Validate layer names inside groups
            for group_name, requested in [
                ("Frames", config.Frames),
                ("Focuses", config.Focuses),
                ("Crops", config.Crops),
            ]:
                if not requested:
                    continue
                group = next((item for item in upper_dir if item.name == group_name), None)
                if group is None:
                    raise ValueError(
                        f"Group '{group_name}' not found inside '{config.base_layer}'"
                    )
                available = [layer.name for layer in group]
                for name in requested:
                    if name not in available:
                        raise ValueError(
                            f"Layer '{name}' not found in '{group_name}'. "
                            f"Available: {available}"
                        )

            # Set visibility recursively
            self._set_visibility_recursive(upper_dir, True)
            layers_to_render: list = []

            for item in upper_dir:
                if item.name == config.base_layer:
                    self._set_visibility_recursive(item, True)
                elif item.name in ("Focuses", "Frames", "Crops"):
                    allowed = group_names_map[item.name]
                    has_visible = False
                    for layer in item:
                        if layer.name in allowed:
                            self._set_visibility_recursive(layer, True)
                            if str(layer.kind) == "shape":
                                layers_to_render.append(layer)
                            has_visible = True
                        else:
                            self._set_visibility_recursive(layer, False)
                    self._set_visibility_recursive(item, has_visible)
                else:
                    self._set_visibility_recursive(item, False)

            canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))

            # Base pixel layer via topil (works even when group was hidden in PSD)
            for item in upper_dir:
                if item.name == config.base_layer and str(item.kind) == "pixel":
                    img = item.topil()
                    if img is not None:
                        canvas.alpha_composite(img, dest=(max(item.left, 0), max(item.top, 0)))

            # Non-shape layers in Focuses
            for item in upper_dir:
                if item.name in ("Focuses",):
                    for layer in item:
                        if layer.name in group_names_map[item.name] and str(layer.kind) != "shape":
                            img = layer.topil()
                            if img is not None:
                                if layer.opacity < 255:
                                    r, g, b, a = img.split()
                                    a = a.point(lambda x: x * layer.opacity // 255)
                                    img = Image.merge("RGBA", (r, g, b, a))
                                canvas.alpha_composite(img, dest=(max(layer.left, 0), max(layer.top, 0)))

            # Shape layers (Frames) — manual render
            for layer in layers_to_render:
                img = self._render_shape_layer(layer, canvas_size)
                if img is not None:
                    canvas.alpha_composite(img)

            # Crop via mask_data bbox
            if config.Crops:
                crop_bbox = None
                for item in upper_dir:
                    if item.name == "Crops":
                        for layer in item:
                            if layer.name in group_names_map["Crops"]:
                                md = layer._record.mask_data
                                if md and md.left is not None:
                                    crop_bbox = (md.left, md.top, md.right, md.bottom)
                                    break
                if crop_bbox:
                    canvas = canvas.crop(crop_bbox)

            # Build output path: {output_dir}/{base_layer}/{Groups}/{names}.webp
            groups_present = []
            if config.Focuses:
                groups_present.append("Focuses")
            if config.Frames:
                groups_present.append("Frames")
            if config.Crops:
                groups_present.append("Crops")

            if groups_present:
                subdir = self.output_dir / config.base_layer / "_".join(groups_present)
                all_names = config.Focuses + config.Frames + config.Crops
                filename = "_".join(all_names) + f".{self.image_ext}"
            else:
                subdir = self.output_dir / config.base_layer
                filename = f"{config.base_layer}.{self.image_ext}"

            subdir.mkdir(parents=True, exist_ok=True)
            output_path = subdir / filename
            canvas.save(output_path, format=self.image_ext)
            return output_path

        raise ValueError(f"base_layer '{config.base_layer}' group not iterable in PSD")

    def terminate(self) -> None:
        """Clear PSD cache."""
        self._cache.clear()
        for tmp_file in list(self._downloaded_tmp):
            try:
                tmp_file.unlink(missing_ok=True)
            finally:
                self._downloaded_tmp.discard(tmp_file)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_psd_source(self, source: str) -> Path:
        """Resolve local path or download http(s) PSD to a temporary file."""
        parsed = urlparse(source)
        if parsed.scheme in {"http", "https"}:
            return self._download_psd(source)

        return Path(source)

    def _download_psd(self, url: str) -> Path:
        """Download PSD URL into a temporary file and return its local path."""
        suffix = Path(urlparse(url).path).suffix or ".psd"
        fd, tmp_name = tempfile.mkstemp(prefix="umda_psd_", suffix=suffix)
        tmp_path = Path(tmp_name)
        with urlopen(url, timeout=60) as response:
            with open(fd, "wb", closefd=True) as out:
                shutil.copyfileobj(response, out)

        self._downloaded_tmp.add(tmp_path)
        return tmp_path

    def _set_visibility_recursive(self, layer, visible: bool) -> None:
        layer._record.flags.visible = not visible
        layer._record.flags.pixel_data_irrelevant = False
        if hasattr(layer, "__iter__"):
            for child in layer:
                self._set_visibility_recursive(child, visible)

    def _render_shape_layer(self, layer, canvas_size: tuple) -> Image.Image | None:
        """Manually render stroke/fill shape layer using origination data."""
        tb = layer._record.tagged_blocks
        vs = tb.get(Tag.VECTOR_STROKE_DATA)
        od = tb.get(Tag.VECTOR_ORIGINATION_DATA)
        if not vs or not od:
            return None

        stroke_data = vs.data
        stroke_enabled = stroke_data.get(b"strokeEnabled", False)
        fill_enabled = stroke_data.get(b"fillEnabled", False)
        stroke_width = int(stroke_data.get(b"strokeStyleLineWidth", 1))

        stroke_color = None
        if stroke_enabled:
            clr = stroke_data.get(b"strokeStyleContent", {}).get(b"Clr ", {})
            if clr:
                stroke_color = (
                    int(clr.get(b"Rd  ", 0)),
                    int(clr.get(b"Grn ", 0)),
                    int(clr.get(b"Bl  ", 0)),
                    255,
                )

        fill_color = None
        if fill_enabled:
            sc_block = tb.get(Tag.SOLID_COLOR_SHEET_SETTING)
            if sc_block:
                clr = sc_block.data.get(b"Clr ", {})
                if clr:
                    fill_color = (
                        int(clr.get(b"Rd  ", 0)),
                        int(clr.get(b"Grn ", 0)),
                        int(clr.get(b"Bl  ", 0)),
                        255,
                    )

        key_list = od.data.get(b"keyDescriptorList", [])
        if not key_list:
            return None

        bbox_data = key_list[0].get(b"keyOriginShapeBBox", {})
        left = int(bbox_data.get(b"Left", layer.left))
        top = int(bbox_data.get(b"Top ", layer.top))
        right = int(bbox_data.get(b"Rght", layer.right))
        bottom = int(bbox_data.get(b"Btom", layer.bottom))

        img = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rectangle(
            [left, top, right - 1, bottom - 1],
            fill=fill_color,
            outline=stroke_color,
            width=stroke_width,
        )
        return img
