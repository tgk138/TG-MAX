from fastapi import FastAPI
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.auth import router as auth_router
from app.api.max import router as max_router
from app.api.migrations import account_router, router as migrations_router
from app.api.pages import router as pages_router
from app.api.telegram import router as tg_router
from app.config import settings


app = FastAPI(title="TG→MAX Migration", version="0.1.0")

# API routes
app.include_router(tg_router)
app.include_router(max_router)
app.include_router(migrations_router)
app.include_router(account_router)
app.include_router(auth_router)

# UI routes
app.include_router(pages_router)
app.mount("/media", StaticFiles(directory=settings.MEDIA_ROOT), name="media")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/favicon.ico")
async def favicon_ico():
    return RedirectResponse(url="/favicon.svg", status_code=302)


@app.get("/favicon.svg")
async def favicon_svg():
    """Product favicon (TG→MAX)."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">'
        '<rect width="32" height="32" rx="6" fill="#1a1a2e"/>'
        '<text x="16" y="22" font-family="system-ui,sans-serif" font-size="14" font-weight="700" fill="#e94560" text-anchor="middle">TG</text>'
        '</svg>'
    )
    return Response(svg, media_type="image/svg+xml")
