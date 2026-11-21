"""Image generation: generate, list, delete. Images are stored as files the user owns."""

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import FileObject, ImageGeneration, User
from ..providers.base import ProviderError
from ..providers.images import ImageService
from ..security import audit, get_current_user, primary_org_id
from ..services.files import UploadRejected, get_storage, validate_upload
from ..services.health import health
from ..services.usage import record_usage
from .chat import _precheck

router = APIRouter(prefix="/api/images", tags=["images"])
_service: ImageService | None = None


def get_image_service() -> ImageService:
    global _service
    if _service is None:
        _service = ImageService(get_settings())
    return _service


class ImageIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    model: str = "auto"
    size: str = "1024x1024"


def _out(g: ImageGeneration) -> dict:
    return {"id": str(g.id), "prompt": g.prompt, "provider": g.provider, "model": g.model, "size": g.size,
            "mock": g.provider == "mock", "file_id": str(g.file_id) if g.file_id else None,
            "url": f"/api/files/{g.file_id}/content" if g.file_id else None, "revised_prompt": g.revised_prompt,
            "cost_usd": g.cost_usd, "created_at": g.created_at.isoformat()}


@router.get("/models")
def image_models(user: User = Depends(get_current_user)):
    svc = get_image_service()
    return {"models": [{"id": m.id, "label": m.label, "provider": m.provider, "sizes": list(m.sizes),
                        "price_per_image_usd": m.price_per_image_usd, "source": m.source,
                        "configured": svc.configured(m.provider), "mock": m.provider == "mock"} for m in svc.models()]}


@router.post("", status_code=201)
async def generate(body: ImageIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _precheck(db, user)
    svc = get_image_service()
    m = svc.resolve(body.model)
    if not m:
        raise HTTPException(status_code=400, detail="No image model is available on this server.")
    if not svc.configured(m.provider):
        raise HTTPException(status_code=400, detail=f"{m.label} is not configured on this server.")
    if body.size not in m.sizes:
        raise HTTPException(status_code=400, detail=f"{m.label} supports these sizes: {', '.join(m.sizes)}.")
    org_id = primary_org_id(db, user)
    start = time.perf_counter()
    try:
        img = await svc.generate(m, body.prompt, body.size)
    except ProviderError as e:
        latency = int((time.perf_counter() - start) * 1000)
        health.record(m.provider, m.model, False, retryable=e.retryable, error=e.code)
        record_usage(db, user_id=user.id, org_id=org_id, feature="images", provider=m.provider, model=m.model,
                     cost_usd=0.0, latency_ms=latency, status="error", error_code=e.code, error_message=e.message)
        db.commit()
        status = 422 if e.code == "content_blocked" else 502
        raise HTTPException(status_code=status, detail=e.message) from e
    latency = int((time.perf_counter() - start) * 1000)
    health.record(m.provider, m.model, True, latency)
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(img.media_type, "png")
    try:
        v = validate_upload(f"image-{uuid.uuid4().hex[:8]}.{ext}", img.data)  # same checks as user uploads
    except UploadRejected as e:
        raise HTTPException(status_code=502, detail=f"The provider returned an invalid image ({e}).") from e
    f = FileObject(user_id=user.id, org_id=org_id, filename=v.filename, content_type=v.content_type, kind=v.kind,
                   size_bytes=v.size, sha256=v.sha256, storage_key=get_storage().put(user.id, img.data))
    db.add(f)
    db.flush()
    cost = m.price_per_image_usd or 0.0
    g = ImageGeneration(user_id=user.id, org_id=org_id, prompt=body.prompt, provider=m.provider, model=m.model,
                        size=body.size, file_id=f.id, revised_prompt=img.revised_prompt, cost_usd=cost)
    db.add(g)
    record_usage(db, user_id=user.id, org_id=org_id, feature="images", provider=m.provider, model=m.model,
                 cost_usd=cost, latency_ms=latency, status="ok")
    audit(db, "image.generated", user_id=user.id, org_id=org_id, target_type="image", target_id=str(g.id))
    db.commit()
    db.refresh(g)
    return _out(g)


@router.get("")
def list_images(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(ImageGeneration).where(ImageGeneration.user_id == user.id)
                      .order_by(ImageGeneration.created_at.desc()).limit(200)).all()
    return [_out(g) for g in rows]


@router.delete("/{image_id}", status_code=204)
def delete_image(image_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    g = db.get(ImageGeneration, image_id)
    if not g or g.user_id != user.id:
        raise HTTPException(status_code=404, detail="Image not found.")
    if g.file_id and (f := db.get(FileObject, g.file_id)):
        get_storage().delete(f.storage_key)
        db.delete(f)
    db.delete(g)
    db.commit()
