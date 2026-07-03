from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field


class AdapterConfigSection(BaseModel):
    output_config_name: str = Field(default="")
    options: dict[str, Any] = Field(default={})
    sections: dict[str, Path] = Field(default={})  # section_name -> file_path


class AdapterConfigMedia(BaseModel):
    media_storage_output: Path
    image_extantion: str = Field(default="webp")
    media_base_url: str = Field(default="")  # e.g. /media — used as URL prefix in MD links


class AdapterConfig(BaseModel):
    doc_output: Path
    mkdocs_path: Optional[Path] = Field(default=None)
    config: Optional[AdapterConfigSection] = Field(default=None)
    media: AdapterConfigMedia
    swap_list: Optional[Path] = Field(default=None)
    vuepress_path: Optional[Path] = Field(default=None)
    vars: Optional[Path] = Field(default=None)  # path to vars.yaml for var injection
    nav: Optional[Path] = Field(default=None)  # path to nav.yaml for navbar/sidebar generation

    model_config = {"extra": "allow"}


class UMDAConfig(BaseModel):
    doc_input: Path
    model_config = {"extra": "allow"}
