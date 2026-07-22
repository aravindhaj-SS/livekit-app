import logging
import os
from contextlib import asynccontextmanager

from fastapi import Cookie, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from api.routes import router as api_router
from core.config import settings
from core.dashboard_auth import dash_token, dash_token_valid
from core.database import init_db
from core.rag import aclose as rag_aclose
from voice.gemini_bridge import router as ws_router

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
    yield
    await rag_aclose()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
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
        resp = RedirectResponse(url="/dashboard", status_code=303)
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
