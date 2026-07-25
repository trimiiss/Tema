import os
from typing import Optional
from app.core.database import get_db


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
    db = get_db()
    db.table("documents").update({"status": "processing"}).eq("id", doc_id).execute()

    raw_text = extract_text_from_file(file_path)

    # Use Document Agent for classification + extraction
    from app.agents.document_agent import run_document_agent
    result = await run_document_agent(doc_id, raw_text, user_id)

    db.table("documents").update({
        "status": "pending",  # stays pending until staff verifies
        "doc_type": result.get("doc_type", "other"),
    }).eq("id", doc_id).execute()

    # Save extracted fields
    fields = result.get("fields", [])
    if fields:
        db.table("document_fields").insert([
            {
                "document_id": doc_id,
                "field_name": f["name"],
                "field_value": f["value"],
                "confidence": f.get("confidence", 0.0),
            }
            for f in fields
        ]).execute()
