import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import FileObject, User
from ..schemas import FileOut
from ..security import audit, client_ip, enforce_rate_limit, get_current_user, primary_org_id
from ..services.files import UploadRejected, get_storage, validate_upload

router = APIRouter(prefix="/api/files", tags=["files"])


def get_owned_file(db: Session, user: User, file_id: uuid.UUID) -> FileObject:
    f = db.get(FileObject, file_id)
    if not f or f.user_id != user.id:
        raise HTTPException(status_code=404, detail="File not found")
    return f


@router.post("", response_model=FileOut, status_code=201)
async def upload_file(request: Request, file: UploadFile = File(...), user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    s = get_settings()
    enforce_rate_limit(f"upload:{user.id}", 30)
    limit = s.max_upload_mb * 1024 * 1024
    # Read at most limit+1 bytes so an oversized upload is rejected without buffering it all.
    data = await file.read(limit + 1)
    try:
        v = validate_upload(file.filename or "file", data)
    except UploadRejected as e:
        audit(db, "file.rejected", user_id=user.id, ip=client_ip(request),
              details={"filename": (file.filename or "")[:200], "reason": str(e)})
        db.commit()
        raise HTTPException(status_code=422, detail=str(e)) from None
    key = get_storage().put(user.id, data)
    obj = FileObject(user_id=user.id, org_id=primary_org_id(db, user), filename=v.filename,
                     content_type=v.content_type, kind=v.kind, size_bytes=v.size, sha256=v.sha256,
                     storage_key=key, extracted_text=v.extracted_text)
    db.add(obj)
    db.flush()
    audit(db, "file.upload", user_id=user.id, org_id=obj.org_id, target_type="file", target_id=str(obj.id),
          ip=client_ip(request), details={"kind": v.kind, "size": v.size})
    db.commit()
    return obj


@router.get("", response_model=list[FileOut])
def list_files(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(FileObject).filter(FileObject.user_id == user.id).order_by(FileObject.created_at.desc()).limit(200).all()


@router.get("/{file_id}/content")
def download_file(file_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    f = get_owned_file(db, user, file_id)
    data = get_storage().get(f.storage_key)
    # Always download as attachment with nosniff so uploaded HTML/SVG can never execute on our origin.
    disposition = "inline" if f.kind == "image" else "attachment"
    return Response(data, media_type=f.content_type, headers={
        "Content-Disposition": f'{disposition}; filename="{f.filename}"',
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
    })


@router.delete("/{file_id}", status_code=204)
def delete_file(file_id: uuid.UUID, request: Request, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    f = get_owned_file(db, user, file_id)
    get_storage().delete(f.storage_key)
    audit(db, "file.delete", user_id=user.id, org_id=f.org_id, target_type="file", target_id=str(f.id),
          ip=client_ip(request))
    db.delete(f)
    db.commit()
    return Response(status_code=204)
