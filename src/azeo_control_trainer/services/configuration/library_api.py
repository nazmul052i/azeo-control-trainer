"""Class reviews use the same permissions, leases and immutable writer as check-in."""
from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field

from azeo_control_trainer.core.configuration.libraries import LibraryRepository


class AdoptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected: list[str] = Field(min_length=1, max_length=1000)
    pin: bool = False
    generation: int | None = Field(default=None, ge=1)


class AdoptionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview: str
    reason: str = Field(min_length=1, max_length=2000)
    command_id: str


def install_routes(app, repository, access):
    libraries = LibraryRepository(repository)

    @app.get("/v1/projects/{project}/libraries")
    def state(project: str, token=Depends(access)):
        return libraries.state(token, project)

    @app.post("/v1/projects/{project}/libraries/preview")
    def preview(project: str, body: AdoptionInput, token=Depends(access)):
        return libraries.preview(token, project, **body.model_dump())

    @app.post("/v1/projects/{project}/libraries/adopt")
    def commit(project: str, body: AdoptionCommand, token=Depends(access)):
        return libraries.commit(token, project, body.preview, body.reason, body.command_id)
