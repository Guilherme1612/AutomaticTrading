"""Pipeline route — kanban-style verdict board."""

from fastapi import APIRouter, Request

from pmacs.web.app import templates

router = APIRouter()


@router.get("/pipeline")
async def pipeline_page(request: Request):
    """Render the pipeline kanban page with verdict columns."""
    return templates.TemplateResponse(
        request=request,
        name="pipeline.html",
        context={
            "page": "pipeline",
            "mode": "SHADOW + PAPER",
            "columns": [
                {"verdict": "STRONG_BUY", "color": "green", "cards": []},
                {"verdict": "BUY", "color": "blue", "cards": []},
                {"verdict": "HOLD", "color": "amber", "cards": []},
                {"verdict": "SKIP", "color": "red", "cards": []},
            ],
            "queue_size": 0,
            "cycles_today": 0,
        },
    )
