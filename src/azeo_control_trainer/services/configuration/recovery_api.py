"""Administrator-only backup artifacts and isolated restore rehearsals."""
import asyncio
from fastapi import Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from azeo_control_trainer.core.configuration.documents import ConfigurationError, Conflict
from azeo_control_trainer.core.configuration.recovery import MAX_EVIDENCE


class BackupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    evidence: list[str] = Field(default_factory=list, max_length=20)
    command_id: str


class RestoreInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: str


def install_routes(app, recovery, access):
    @app.get("/v1/recovery")
    def state(token=Depends(access)):
        return recovery.state(token)

    @app.put("/v1/recovery/evidence/{identity}")
    async def evidence(identity: str, request: Request, kind: str, token=Depends(access)):
        path = await asyncio.to_thread(recovery.evidence_path, token, identity)
        if kind not in {"history", "training", "journal", "backup"}:
            raise ConfigurationError("Unsupported evidence type")
        if path.exists():
            raise Conflict("Use a new evidence upload identity")
        size = 0
        created = False
        try:
            with path.open("xb") as output:
                created = True
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_EVIDENCE:
                        raise ConfigurationError("Evidence archive exceeds 2 GB")
                    await asyncio.to_thread(output.write, chunk)
            return await asyncio.to_thread(recovery.accept_evidence, token, identity, kind)
        except BaseException:
            if created:
                path.unlink(missing_ok=True)
            raise

    @app.post("/v1/recovery/backups")
    def backup(body: BackupInput, token=Depends(access)):
        return recovery.create(token, body.name, body.evidence, body.command_id)

    @app.get("/v1/recovery/backups/{identity}/download")
    def download(identity: str, token=Depends(access)):
        return FileResponse(recovery.export(token, identity), media_type="application/zip", filename="azeo-configuration-backup.zip")

    @app.post("/v1/recovery/backups/{identity}/rehearse")
    def restore(identity: str, body: RestoreInput, token=Depends(access)):
        return recovery.rehearse(token, identity, body.command_id)
