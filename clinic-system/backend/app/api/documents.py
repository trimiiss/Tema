import os
import uuid
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from app.core.auth import require_roles
from app.core.config import upload_path
from app.core.database import get_db, execute_with_retry
from app.core.audit import log_action
from app.core.tasks import spawn
from app.models.schemas import DocumentOut, DocumentFieldOut
from app.services.ocr_service import process_document_async

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("/", response_model=list[DocumentOut])
def list_documents(
    patient_id: str = "",
    user: dict = Depends(require_roles("admin", "receptionist", "doctor")),
):
    db = get_db()
    q = db.table("documents").select("*")
    if patient_id:
        q = q.eq("patient_id", patient_id)
    resp = execute_with_retry(q.order("created_at", desc=True))
    return resp.data


@router.post("/upload", response_model=DocumentOut, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    patient_id: str = Form(default=""),
    user: dict = Depends(require_roles("admin", "receptionist")),
):
    allowed_types = {"application/pdf", "image/png", "image/jpeg", "image/tiff"}
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=415, detail="Unsupported file type")

    upload_dir = upload_path()
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename or "file")[1] or ".bin"
    storage_path = str(upload_dir / f"{file_id}{ext}")

    contents = await file.read()
    with open(storage_path, "wb") as f:
        f.write(contents)

    db = get_db()
    doc_data = {
        "filename": file.filename,
        "storage_path": storage_path,
        "status": "pending",
        "uploaded_by": user["id"],
    }
    if patient_id:
        doc_data["patient_id"] = patient_id

    resp = execute_with_retry(db.table("documents").insert(doc_data))
    doc = resp.data[0]
    log_action(user["id"], "upload", "document", doc["id"], {"filename": file.filename})

    # Kick off async processing (non-blocking)
    spawn(process_document_async(doc["id"], storage_path, user["id"]),
          name=f"document-ocr:{doc['id']}")

    return doc


@router.get("/{doc_id}", response_model=DocumentOut)
def get_document(
    doc_id: str,
    user: dict = Depends(require_roles("admin", "receptionist", "doctor")),
):
    db = get_db()
    resp = execute_with_retry(db.table("documents").select("*").eq("id", doc_id).maybe_single())
    if not resp.data:
        raise HTTPException(status_code=404, detail="Document not found")
    return resp.data


@router.get("/{doc_id}/fields", response_model=list[DocumentFieldOut])
def get_document_fields(
    doc_id: str,
    user: dict = Depends(require_roles("admin", "receptionist", "doctor")),
):
    db = get_db()
    resp = execute_with_retry(db.table("document_fields").select("*").eq("document_id", doc_id))
    return resp.data


@router.post("/{doc_id}/verify")
def verify_document(
    doc_id: str,
    user: dict = Depends(require_roles("admin", "receptionist")),
):
    db = get_db()
    execute_with_retry(db.table("documents").update({"status": "verified"}).eq("id", doc_id))
    execute_with_retry(db.table("document_fields").update({"verified_by": user["id"]}).eq("document_id", doc_id))
    log_action(user["id"], "verify", "document", doc_id)
    return {"status": "verified"}


@router.post("/{doc_id}/reject")
def reject_document(
    doc_id: str,
    user: dict = Depends(require_roles("admin", "receptionist")),
):
    db = get_db()
    execute_with_retry(db.table("documents").update({"status": "rejected"}).eq("id", doc_id))
    log_action(user["id"], "reject", "document", doc_id)
    return {"status": "rejected"}
