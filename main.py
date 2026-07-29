import logging
import os
from contextlib import asynccontextmanager

from fastapi import Cookie, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from api.routes import router as api_router
from core.config import settings
from core.dashboard_auth import dash_token, dash_token_valid
from core.database import close_db, init_db
from core.rag import aclose as rag_aclose
from voice.gemini_bridge import router as ws_router
from voice.warm_pool import start_inbound_warm_pool, stop_inbound_warm_pool

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/app.log"),
    ],
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    start_inbound_warm_pool()
    yield
    await stop_inbound_warm_pool()
    await rag_aclose()
    await close_db()


class NoCacheStaticFiles(StaticFiles):
    """Plain StaticFiles sends no Cache-Control header at all, so browsers
    fall back to heuristic caching and can keep serving stale dashboard.css/
    dashboard-common.js for a long time after a deploy — with no visible sign
    anything is wrong, since the page still loads fine, just with old assets.
    no-cache forces revalidation (If-None-Match) on every load; ETag support
    means an unchanged file still comes back as a cheap 304, not a re-download."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app = FastAPI(lifespan=lifespan)
app.mount("/static", NoCacheStaticFiles(directory="static"), name="static")
app.include_router(api_router, prefix="/api")
app.include_router(ws_router)


@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/login")
async def login_page():
    return FileResponse("static/login.html")


@app.post("/login")
async def login_submit(username: str = Form(...), password: str = Form(...)):
    if username == settings.DASHBOARD_ADMIN_USERNAME and password == settings.DASHBOARD_ADMIN_PASSWORD:
        # Master Dashboard is the landing page after login — /dashboard
        # itself still means Outbound unchanged, only where login sends you
        # is different.
        resp = RedirectResponse(url="/dashboard/master", status_code=303)
        resp.set_cookie("dash_auth", dash_token(), httponly=True, samesite="lax")
        return resp
    return HTMLResponse("<p>Invalid credentials. <a href='/login'>Try again</a></p>", status_code=401)


@app.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login")
    resp.delete_cookie("dash_auth")
    return resp


@app.get("/dashboard")
async def dashboard_page(dash_auth: str | None = Cookie(default=None)):
    if not dash_token_valid(dash_auth):
        return RedirectResponse(url="/login")
    return FileResponse("static/dashboard.html")


@app.get("/dashboard/inbound")
async def dashboard_inbound_page(dash_auth: str | None = Cookie(default=None)):
    if not dash_token_valid(dash_auth):
        return RedirectResponse(url="/login")
    return FileResponse("static/dashboard_inbound.html")


@app.get("/dashboard/master")
async def dashboard_master_page(dash_auth: str | None = Cookie(default=None)):
    if not dash_token_valid(dash_auth):
        return RedirectResponse(url="/login")
    return FileResponse("static/dashboard_master.html")


@app.get("/dashboard/meetings")
async def dashboard_meetings_page(dash_auth: str | None = Cookie(default=None)):
    if not dash_token_valid(dash_auth):
        return RedirectResponse(url="/login")
    return FileResponse("static/dashboard_meetings.html")


@app.get("/dashboard/leads")
async def dashboard_leads_page(dash_auth: str | None = Cookie(default=None)):
    if not dash_token_valid(dash_auth):
        return RedirectResponse(url="/login")
    return FileResponse("static/dashboard_leads.html")
