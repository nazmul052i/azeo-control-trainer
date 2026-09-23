"""Release commands and separately authenticated runtime target endpoints."""
from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field

from azeo_control_trainer.core.configuration.releases import ReleaseRepository


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Review(Input):
    paths: list[str] = Field(min_length=1, max_length=1000)


class Release(Input):
    preview: str
    reason: str = Field(min_length=1, max_length=2000)
    command_id: str


class Target(Input):
    name: str = Field(min_length=1, max_length=160)


class Session(Input):
    boot: str


class Observation(Session):
    report: dict
    online: bool = True


class Acknowledgment(Session):
    state: str
    receipt: dict


class Deployment(Input):
    release: str
    target: str
    components: list[str] = Field(min_length=1, max_length=2)
    command_id: str


class Upload(Input):
    preview: str
    selected: list[str] = Field(min_length=1, max_length=10000)
    reason: str = Field(min_length=1, max_length=1500)
    command_id: str


def install_routes(app, repository, access):
    releases = ReleaseRepository(repository)
    from azeo_control_trainer.core.configuration.uploads import UploadRepository
    uploads = UploadRepository(repository)

    @app.post("/v1/projects/{project}/runtime-targets/{target}/upload-preview")
    def upload_preview(project: str, target: str, token=Depends(access)):
        return uploads.preview(token, project, target)

    @app.post("/v1/projects/{project}/uploads")
    def upload(project: str, body: Upload, token=Depends(access)):
        return uploads.commit(token, project, body.preview, body.selected, body.reason, body.command_id)

    @app.get("/v1/projects/{project}/releases/state")
    def state(project: str, token=Depends(access)):
        return releases.state(token, project)

    @app.post("/v1/projects/{project}/releases/preview")
    def preview(project: str, body: Review, token=Depends(access)):
        return releases.preview(token, project, body.paths)

    @app.post("/v1/projects/{project}/releases")
    def create(project: str, body: Release, token=Depends(access)):
        return releases.create(token, project, body.preview, body.reason, body.command_id)

    @app.get("/v1/projects/{project}/releases/{release}")
    def bundle(project: str, release: str, token=Depends(access)):
        return releases.bundle(token, project, release)

    @app.post("/v1/projects/{project}/runtime-targets")
    def register(project: str, body: Target, token=Depends(access)):
        return releases.register_target(token, project, body.name)

    @app.post("/v1/projects/{project}/deployments")
    def enqueue(project: str, body: Deployment, token=Depends(access)):
        return releases.enqueue(token, project, body.release, body.target, body.components, body.command_id)

    @app.post("/v1/projects/{project}/deployments/{job}/retry")
    def retry(project: str, job: str, token=Depends(access)):
        return releases.retry(token, project, job)

    @app.post("/v1/projects/{project}/deployments/{job}/cancel")
    def cancel(project: str, job: str, token=Depends(access)):
        return releases.cancel(token, project, job)

    @app.post("/v1/runtime-targets/{target}/hello")
    def hello(target: str, body: Session, token=Depends(access)):
        return releases.hello(token, target, body.boot)

    @app.post("/v1/runtime-targets/{target}/report")
    def report(target: str, body: Observation, token=Depends(access)):
        return releases.report(token, target, body.boot, body.report, online=body.online)

    @app.post("/v1/runtime-targets/{target}/pending")
    def pending(target: str, body: Session, token=Depends(access)):
        return releases.pending(token, target, body.boot)

    @app.post("/v1/runtime-targets/{target}/jobs/{job}")
    def acknowledge(target: str, job: str, body: Acknowledgment, token=Depends(access)):
        return releases.acknowledge(token, target, body.boot, job, body.state, body.receipt)
