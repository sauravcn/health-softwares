"""HealthSoftwares — home-care agency back-office API + admin UI."""
from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from . import admin
from .db import init_db
from .routers import evv, finance, operations, people, scheduling

app = FastAPI(title="HealthSoftwares", version="0.1.0")

app.include_router(people.router, prefix="/api", tags=["people"])
app.include_router(scheduling.router, prefix="/api", tags=["scheduling"])
app.include_router(evv.router, prefix="/api", tags=["evv"])
app.include_router(finance.router, prefix="/api", tags=["finance"])
app.include_router(operations.router, prefix="/api", tags=["operations"])
app.include_router(admin.router, tags=["admin"])


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse("/admin")
