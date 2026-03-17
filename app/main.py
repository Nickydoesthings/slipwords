# FastAPI app entry point; defines all routes (logic lives in search.py).
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.models import get_session
from app.search import run_search

# Templates and static files are under the app package.
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI()

# Serve CSS and other static assets at /static.
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    """Render the homepage with search bar and short about text."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/search", response_class=HTMLResponse)
def search(
    request: Request,
    q: str = "",
    db: Session = Depends(get_session),
) -> HTMLResponse:
    """
    Run search using query string `q`. Detection and query logic are in search.py.
    Renders the results template with results and query for display.
    Empty or whitespace-only queries redirect back to the homepage.
    """
    if not q or not q.strip():
        return RedirectResponse(url="/", status_code=303)

    results, query_type = run_search(db, q)
    return templates.TemplateResponse(
        "results.html",
        {
            "request": request,
            "query": q,
            "results": results,
            "query_type": query_type,
        },
    )
