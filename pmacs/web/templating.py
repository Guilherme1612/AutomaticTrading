"""Shared Jinja2 templates instance — imported by routes to avoid circular imports."""

from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape

_jinja_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=select_autoescape(["html", "htm"]),
)
templates = Jinja2Templates(env=_jinja_env)
