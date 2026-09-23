"""Shared baseline commands; process restore remains an explicit operator action."""
from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field
from azeo_control_trainer.core.configuration.training import TrainingRepository


class BaselineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    release: str
    name: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=160)
    command_id: str
    exercise: dict | None = None
    snapshot: dict | None = None


class CloneInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    command_id: str


def install_routes(app, repository, access):
    training = TrainingRepository(repository)

    @app.get("/v1/projects/{project}/baselines")
    def baselines(project: str, token=Depends(access)):
        return training.list(token, project)

    @app.post("/v1/projects/{project}/baselines")
    def create(project: str, body: BaselineInput, token=Depends(access)):
        return training.create(token, project, body.release, body.name, body.reason, body.command_id,
                               exercise=body.exercise, snapshot=body.snapshot)

    @app.get("/v1/projects/{project}/baselines/{baseline}")
    def read(project: str, baseline: str, token=Depends(access)):
        return training.read(token, project, baseline)

    @app.post("/v1/projects/{project}/baselines/{baseline}/clone")
    def clone(project: str, baseline: str, body: CloneInput, token=Depends(access)):
        return training.clone(token, project, baseline, body.name, body.command_id)

    @app.get("/v1/projects/{project}/training-context")
    def context(project: str, token=Depends(access)):
        return training.context(token, project)
