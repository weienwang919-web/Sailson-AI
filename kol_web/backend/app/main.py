from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.kols import router as kol_router
from app.api.official_accounts import router as official_router
from app.database import init_db

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

app = FastAPI(title="KOL List Manager", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_proxy_token(request: Request, call_next):
    proxy_token = os.getenv("KOL_PROXY_TOKEN")
    if proxy_token and request.url.path != "/api/health":
        if request.headers.get("X-KOL-Proxy-Token") != proxy_token:
            return JSONResponse({"detail": "Forbidden"}, status_code=403)
    return await call_next(request)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(kol_router)
app.include_router(official_router)
