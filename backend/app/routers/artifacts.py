import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Artifact, ArtifactVersion, User
from ..schemas import ArtifactDetail, ArtifactEditIn, ArtifactOut, ArtifactRestoreIn
from ..security import get_current_user

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


def get_owned_artifact(db: Session, user: User, artifact_id: uuid.UUID) -> Artifact:
    a = db.get(Artifact, artifact_id)
    if not a or a.user_id != user.id:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return a


@router.get("", response_model=list[ArtifactOut])
def list_artifacts(conversation_id: uuid.UUID | None = Query(default=None), user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    q = db.query(Artifact).filter(Artifact.user_id == user.id)
    if conversation_id:
        q = q.filter(Artifact.conversation_id == conversation_id)
    return q.order_by(Artifact.created_at.desc()).limit(200).all()


@router.get("/{artifact_id}", response_model=ArtifactDetail)
def get_artifact(artifact_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_owned_artifact(db, user, artifact_id)


@router.post("/{artifact_id}/versions", response_model=ArtifactDetail)
def save_version(artifact_id: uuid.UUID, body: ArtifactEditIn, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    a = get_owned_artifact(db, user, artifact_id)
    nxt = max((v.version for v in a.versions), default=0) + 1
    a.versions.append(ArtifactVersion(version=nxt, content=body.content, source="user"))
    a.current_version = nxt
    if body.title:
        a.title = body.title
    db.commit()
    db.refresh(a)
    return a


@router.post("/{artifact_id}/restore", response_model=ArtifactDetail)
def restore_version(artifact_id: uuid.UUID, body: ArtifactRestoreIn, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Restoring never deletes history: it appends a copy of the old version as the newest one."""
    a = get_owned_artifact(db, user, artifact_id)
    old = next((v for v in a.versions if v.version == body.version), None)
    if not old:
        raise HTTPException(status_code=404, detail="Version not found")
    nxt = max(v.version for v in a.versions) + 1
    a.versions.append(ArtifactVersion(version=nxt, content=old.content, source="restore"))
    a.current_version = nxt
    db.commit()
    db.refresh(a)
    return a


@router.get("/{artifact_id}/download")
def download(artifact_id: uuid.UUID, version: int | None = None, user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    a = get_owned_artifact(db, user, artifact_id)
    want = version or a.current_version
    v = next((v for v in a.versions if v.version == want), None)
    if not v:
        raise HTTPException(status_code=404, detail="Version not found")
    ext, media = ("svg", "image/svg+xml") if a.kind == "svg" else ("html", "text/html")
    name = "".join(c if c.isalnum() or c in "-_" else "-" for c in a.title)[:60] or "artifact"
    return Response(v.content, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{name}-v{v.version}.{ext}"',
        "X-Content-Type-Options": "nosniff",
    })
