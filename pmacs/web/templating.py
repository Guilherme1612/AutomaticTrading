"""Shared Jinja2 templates instance — imported by routes to avoid circular imports."""

from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape


def _current_mode_context(request) -> dict:
    """Inject the real operating mode into every rendered page.

    The header badge in base.html relies on ``mode``. Several routes used to
    hardcode ``SHADOW + PAPER`` or omit the variable entirely, leaving the badge
    empty or stale. This processor reads the latest mode_history row once per
    request and falls back to the install default when the DB is unavailable.
    """
    try:
        from pmacs.web.config import get_config
        from pmacs.web import data as _data_layer

        cfg = get_config()
        db = _data_layer.get_readonly_db(cfg.sqlite_path)
        try:
            mode = _data_layer.get_current_mode(db)
        finally:
            db.close()
    except Exception:
        mode = "SHADOW + PAPER"
    return {"mode": mode}


def _format_xy_poly(point):
    """Render an ``{"x", "y", ...}`` point as an SVG polyline coordinate pair.

    Used by the ``sparkline_svg`` macro in ``ticker_detail.html`` via
    ``points|map('format_xy_poly')|join(' ')`` to build the ``points`` attr
    of a ``<polyline>``. Returns ``"x,y"`` with 2-decimal precision to match the
    rest of the macro's coordinate formatting.
    """
    return f"{point['x']:.2f},{point['y']:.2f}"


_jinja_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=select_autoescape(["html", "htm"]),
)
_jinja_env.filters["format_xy_poly"] = _format_xy_poly
templates = Jinja2Templates(env=_jinja_env, context_processors=[_current_mode_context])
