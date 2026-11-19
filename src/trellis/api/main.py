"""
src/trellis/api/main.py
Tier 9 FastAPI Application Factory, Lifespan Graph Ingestion, CORS & Exception Handlers.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from trellis.api.deps import TrellisServiceContainer, get_container
from trellis.api.routers import auth, author, diagnostic, parent, practice, session
from trellis.api.websocket import latency_stream
from trellis.domain.models import CourseCatalogItem, StudentProfile
from trellis.ports.storage_port import TrellisStoragePort

DEFAULT_GRAPH_PATH = Path(__file__).resolve().parents[3] / "data" / "clean_spec" / "trellis_knowledge_graph.json"
STATIC_DIR = Path(__file__).resolve().parent / "static"


class RegisterStudentRequest(BaseModel):
    student_id: str
    enrolled_course_id: str = "CRS_ALG1"
    daily_xp_target: int = 40


def create_app(
    storage: Optional[TrellisStoragePort] = None,
    auto_ingest: bool = True,
    graph_path: Optional[Path] = None,
) -> FastAPI:
    """
    Constructs the Trellis FastAPI application wired to a single standalone
    TrellisStoragePort backend ('memory' | 'relational' | 'graph').
    """
    container = TrellisServiceContainer(storage=storage)
    resolved_graph_path = graph_path or DEFAULT_GRAPH_PATH

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI):
        if auto_ingest and resolved_graph_path.exists():
            if not container.storage.get_course_catalog():
                container.ingestion.ingest_from_file(resolved_graph_path, validate_first=True)
        yield

    application = FastAPI(
        title="Trellis K-16 Adaptive Mathematics API",
        version="0.1.0",
        description=(
            "Deterministic mastery-based math learning engine (Tiers 0-8) "
            "exposed over REST and WebSockets."
        ),
        lifespan=lifespan,
    )
    application.state.container = container

    application.add_middleware(
        CORSMiddleware,
        allow_origins=os.getenv("TRELLIS_CORS_ORIGINS", "*").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.exception_handler(KeyError)
    async def handle_key_error(_request: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(ValueError)
    async def handle_value_error(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @application.exception_handler(IndexError)
    async def handle_index_error(_request: Request, exc: IndexError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @application.get("/health", tags=["System"])
    def health_check() -> Dict[str, Any]:
        catalog = container.storage.get_course_catalog()
        return {
            "status": "ok",
            "db_backend": os.getenv("DB_BACKEND", "memory"),
            "courses_loaded": len(catalog),
        }

    @application.get("/api/v1/courses", response_model=List[CourseCatalogItem], tags=["System"])
    def list_courses() -> List[CourseCatalogItem]:
        """Lists all configured courses in the curriculum catalog."""
        return container.storage.get_course_catalog()

    @application.get("/api/v1/students/{student_id}", response_model=StudentProfile, tags=["System"])
    def get_student(student_id: str) -> StudentProfile:
        """Retrieves a student's profile and decoupled progress/XP metrics."""
        return container.storage.get_student_profile(student_id)

    @application.post("/api/v1/students", response_model=StudentProfile, tags=["System"])
    def register_student(payload: RegisterStudentRequest) -> StudentProfile:
        """Creates or updates a student profile for API & Swagger testing."""
        profile = StudentProfile(
            student_id=payload.student_id,
            enrolled_course_id=payload.enrolled_course_id,
            daily_xp_target=payload.daily_xp_target,
        )
        container.storage.upsert_student_profile(profile)
        return profile

    application.include_router(auth.router, prefix="/api/v1")
    application.include_router(author.router, prefix="/api/v1")
    application.include_router(session.router, prefix="/api/v1")
    application.include_router(practice.router, prefix="/api/v1")
    application.include_router(diagnostic.router, prefix="/api/v1")
    application.include_router(parent.router, prefix="/api/v1")
    application.include_router(latency_stream.router)

    if STATIC_DIR.exists():
        application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
        application.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="ui")

    return application


app = create_app()
