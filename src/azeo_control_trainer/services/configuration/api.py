"""Authenticated engineering commands and deployment evidence, never scan-time data."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from azeo_control_trainer.core.configuration.documents import (
    ConfigurationError, Conflict, Forbidden, MAX_PROJECT_BYTES, Missing, prepare_import,
)
from azeo_control_trainer.core.configuration.repository import Repository


class FileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=1000)
    content: str = Field(max_length=22369624)


class ImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    files: list[FileInput] = Field(min_length=1, max_length=10000)


class ImportCommand(ImportInput):
    expected_generation: int = Field(ge=0)
    command_id: str


class EditInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=1000)
    new_path: str = Field(default="", max_length=1000)
    expected_revision: int = Field(ge=0)
    content: str | None = Field(default=None, max_length=22369624)


class EditPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edits: list[EditInput] = Field(min_length=1, max_length=1000)


class EditCommand(EditPreview):
    session: str
    command_id: str
    reason: str = Field(min_length=1, max_length=2000)
    expected_generation: int | None = Field(default=None, ge=0)


class LeaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session: str
    paths: list[str] = Field(max_length=1000)
    release: bool = False


class ForkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    command_id: str


class RenameInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    module: str = Field(min_length=1, max_length=160)
    new_name: str = Field(min_length=1, max_length=160)


class BodyLimit:
    """Bound chunked uploads too; Content-Length alone is not a memory limit."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "PUT" and scope["path"].startswith("/v1/recovery/evidence/"):
            return await self.app(scope, receive, send)
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > MAX_PROJECT_BYTES * 4 // 3 + 4 * 1024 * 1024:
                return await JSONResponse({"detail": "Snapshot upload is too large"},
                                          status_code=413)(scope, receive, send)
            chunks.append(message)
            if not message.get("more_body", False):
                break
        index = 0

        async def replay():
            nonlocal index
            if index < len(chunks):
                message = chunks[index]
                index += 1
                return message
            return await receive()

        await self.app(scope, replay, send)


def create_app(repository: Repository, *, recovery=None) -> FastAPI:
    import psycopg
    from fastapi.exceptions import RequestValidationError
    from azeo_control_trainer.core.configuration.editing import EditingRepository
    editing = EditingRepository(repository)

    @asynccontextmanager
    async def lifespan(_app):
        try:
            repository.open_pool()
            yield
        finally:
            repository.close_pool()

    app = FastAPI(title="Azeo Configuration Service", version="1.0", lifespan=lifespan, docs_url=None,
                  redoc_url=None, openapi_url=None)
    app.add_middleware(BodyLimit)

    @app.exception_handler(ConfigurationError)
    async def configuration_error(_request, error):
        code = 409 if isinstance(error, Conflict) else 403 if isinstance(error, Forbidden) else (
            404 if isinstance(error, Missing) else 422)
        return JSONResponse({"detail": str(error)}, status_code=code)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error):
        # Pydantic's default response echoes the input, which can be an entire project.
        return JSONResponse({"detail": "Invalid configuration request fields"}, status_code=422)

    @app.exception_handler(psycopg.Error)
    async def database_error(_request, _error):
        return JSONResponse({"detail": "Configuration database unavailable or transaction refused; "
                              "no partial snapshot was committed"}, status_code=503)

    def access(authorization: str = Header(default="")):
        if not authorization.startswith("Bearer "):
            raise Forbidden("Configuration-service sign-in is required")
        token = authorization[7:]
        repository.authenticate(token)
        return token

    from .release_api import install_routes
    install_routes(app, repository, access)
    from .library_api import install_routes as install_library_routes
    install_library_routes(app, repository, access)
    from .training_api import install_routes as install_training_routes
    install_training_routes(app, repository, access)
    if recovery is not None:
        from .recovery_api import install_routes as install_recovery_routes
        install_recovery_routes(app, recovery, access)

    @app.get("/v1/status")
    def status(token=Depends(access)):
        return {"api_version": 1, "mode": "project_specific", "modes": ["file_shadow", "repository"],
                "identity": repository.authenticate(token)}

    @app.get("/v1/projects")
    def projects(token=Depends(access)):
        return repository.projects(token)

    @app.post("/v1/imports/preview")
    def preview(body: ImportInput, token=Depends(access)):
        prepared = prepare_import([f.model_dump() for f in body.files])
        return repository.preview(token, body.name, prepared)

    @app.post("/v1/imports")
    def import_snapshot(body: ImportCommand, token=Depends(access)):
        prepared = prepare_import([f.model_dump() for f in body.files])
        return repository.import_snapshot(token, body.name, prepared,
                                          expected_generation=body.expected_generation,
                                          command_id=body.command_id)

    @app.get("/v1/projects/{project}/objects")
    def objects(project: str, q: str = "", limit: int = Query(200, ge=1, le=500),
                offset: int = Query(0, ge=0, le=1000000), token=Depends(access)):
        return repository.objects(token, project, query=q, limit=limit, offset=offset)

    @app.get("/v1/projects/{project}/tags")
    def tags(project: str, q: str = "", limit: int = Query(200, ge=1, le=500),
             offset: int = Query(0, ge=0, le=1000000), token=Depends(access)):
        return repository.tags(token, project, query=q, limit=limit, offset=offset)

    @app.get("/v1/projects/{project}/catalog")
    def catalog(project: str, known: str = Query("", max_length=200), token=Depends(access)):
        import json
        from datetime import date, datetime
        from uuid import UUID
        from fastapi.responses import Response

        def scalar(value):
            if isinstance(value, (date, datetime)):
                return value.isoformat()
            if isinstance(value, UUID):
                return str(value)
            raise TypeError(f"Unsupported catalog scalar: {type(value).__name__}")

        # This projection already consists of JSON values plus SQL timestamps/IDs.
        # FastAPI's generic recursive encoder took seconds on 34,000 tags, causing
        # the browser's initial load to time out before headers arrived.
        return Response(json.dumps(repository.catalog(token, project, known=known), default=scalar,
                                   allow_nan=False, separators=(",", ":")),
                        media_type="application/json")

    @app.get("/v1/projects/{project}/objects/{object_id}/revisions")
    def revisions(project: str, object_id: str, limit: int = Query(200, ge=1, le=500),
                  offset: int = Query(0, ge=0, le=1000000), token=Depends(access)):
        return repository.revisions(token, project, object_id, limit=limit, offset=offset)

    @app.get("/v1/projects/{project}/objects/{object_id}")
    def document(project: str, object_id: str, revision: int | None = Query(None, ge=1),
                 token=Depends(access)):
        return repository.document(token, project, object_id, revision)

    @app.get("/v1/projects/{project}/audit")
    def audit(project: str, after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500),
              token=Depends(access)):
        return repository.audit(token, project, after=after, limit=limit)

    @app.get("/v1/projects/{project}/export")
    def export(project: str, generation: int | None = Query(None, ge=1), token=Depends(access)):
        return repository.export(token, project, generation=generation)

    @app.post("/v1/projects/{project}/editing/fork")
    def fork(project: str, body: ForkInput, token=Depends(access)):
        return editing.fork(token, project, body.name, body.command_id)

    @app.get("/v1/projects/{project}/editing/state")
    def edit_state(project: str, token=Depends(access)):
        return editing.state(token, project)

    @app.post("/v1/projects/{project}/editing/lease")
    def lease(project: str, body: LeaseInput, token=Depends(access)):
        return editing.lease(token, project, body.session, body.paths, release=body.release)

    @app.post("/v1/projects/{project}/editing/preview")
    def edit_preview(project: str, body: EditPreview, token=Depends(access)):
        return editing.preview(token, project, [e.model_dump() for e in body.edits])

    @app.post("/v1/projects/{project}/editing/checkin")
    def checkin(project: str, body: EditCommand, token=Depends(access)):
        return editing.checkin(token, project, body.session, [e.model_dump() for e in body.edits],
                               body.reason, body.command_id, expected_generation=body.expected_generation)

    @app.post("/v1/projects/{project}/editing/rename")
    def rename(project: str, body: RenameInput, token=Depends(access)):
        return editing.rename_preview(token, project, body.module, body.new_name)

    return app
