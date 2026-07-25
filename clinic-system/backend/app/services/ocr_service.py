import os
from typing import Optional
from app.core.database import get_db, execute_with_retry


def extract_text_from_file(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".pdf":
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                return "\n".join(page.extract_text() or "" for page in pdf.pages)
        else:
            import pytesseract
            from PIL import Image
            img = Image.open(file_path)
            return pytesseract.image_to_string(img)
    except Exception as e:
        return f"[OCR error: {e}]"


async def process_document_async(doc_id: str, file_path: str, user_id: str) -> None:
    """Classify an uploaded document and store the fields read off it.

    Every query goes through `execute_with_retry`. This runs as a background
    task with several seconds of OCR and LLM work between its queries, so the
    pooled connection is idle exactly long enough for Supabase to send GOAWAY —
    the last insert died as `RemoteProtocolError: <ConnectionTerminated>` and the
    extracted fields were lost while the document still showed as processed.
    """
    db = get_db()
    execute_with_retry(db.table("documents").update({"status": "processing"}).eq("id", doc_id))

    raw_text = extract_text_from_file(file_path)

    # Use Document Agent for classification + extraction
    from app.agents.document_agent import run_document_agent
    result = await run_document_agent(doc_id, raw_text, user_id)

    execute_with_retry(db.table("documents").update({
        "status": "pending",  # stays pending until staff verifies
        "doc_type": result.get("doc_type", "other"),
    }).eq("id", doc_id))

    # Save extracted fields. Clear first so the write is idempotent: a GOAWAY
    # can drop the *response* to an insert that already committed, and the retry
    # then inserts the same rows again — which is exactly how this document came
    # back with every field listed twice. Processing is one-shot per document,
    # so replacing the set is also the correct semantics on a re-run.
    fields = result.get("fields", [])
    if fields:
        execute_with_retry(db.table("document_fields").delete().eq("document_id", doc_id))
        execute_with_retry(db.table("document_fields").insert([
            {
                "document_id": doc_id,
                "field_name": f["name"],
                "field_value": f["value"],
                "confidence": f.get("confidence", 0.0),
            }
            for f in fields
        ]))
