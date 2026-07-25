import os
from typing import Any, Dict, List, Optional
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


def dedupe_fields(fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse repeated field names, keeping the most confident value.

    Defence in depth, not the fix for the duplication incident — that was the
    retry (see `process_document_async`). GPT-4o is asked for a fixed list of
    names but returns a free-form array, and nothing stops it emitting one name
    twice; extraction was re-run against the live model on the documents that
    duplicated and it returned each name once, so this has not been observed
    here. It stays because the field set *is* a mapping of name to value, and
    because migration 003 makes a repeated name a hard error rather than a
    cosmetic one.
    """
    best: Dict[str, Dict[str, Any]] = {}
    for f in fields:
        name = f.get("name")
        if not name:
            continue
        prev = best.get(name)
        if prev is None or (f.get("confidence") or 0.0) > (prev.get("confidence") or 0.0):
            # Assigning to an existing key keeps its original position, so the
            # model's field order survives the collapse.
            best[name] = f
    return list(best.values())


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

    # Save extracted fields. Three insurance uploads each stored all four of
    # their fields twice, byte-identical in both value and confidence. The
    # audit log records one upload per document and the live model returns each
    # name once, so neither double-processing nor the model is responsible:
    # the same four-row insert executed twice inside one run, which only
    # `execute_with_retry` can do — an insert that committed and lost its
    # response to a GOAWAY is re-sent as a fresh insert.
    #
    # So the write is keyed rather than blind. `upsert` on
    # (document_id, field_name) makes a re-sent insert overwrite instead of
    # double; `delete` clears fields left over from a previous run that
    # classified the document as a different type, which the upsert alone would
    # strand; `dedupe_fields` guards the model repeating a name.
    #
    # Migration 003 is a **prerequisite**, not a belt-and-braces extra: it creates
    # the unique index the `on_conflict` key names, and Postgres rejects an
    # ON CONFLICT with no matching index. Without it this write fails and the
    # document is left with no extracted fields at all.
    fields = dedupe_fields(result.get("fields", []))
    if fields:
        execute_with_retry(db.table("document_fields").delete().eq("document_id", doc_id))
        execute_with_retry(db.table("document_fields").upsert(
            [
                {
                    "document_id": doc_id,
                    "field_name": f["name"],
                    "field_value": f["value"],
                    "confidence": f.get("confidence", 0.0),
                }
                for f in fields
            ],
            on_conflict="document_id,field_name",
        ))
