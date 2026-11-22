"""The static HTML viewer: one self-contained file, the report embedded as JSON."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any


def render_viewer(report: dict[str, Any]) -> str:
    template = files("sessionfish").joinpath("viewer.html").read_text(encoding="utf-8")
    data = json.dumps(report, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")
    return template.replace("/*__DATA__*/", data)


def write_viewer(report: dict[str, Any], path: Path) -> None:
    path.write_text(render_viewer(report), encoding="utf-8")
