"""Dashboard chatbot route."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ...chat.files import ChatFileError, parse_upload
from ...chat.service import complete_chat_response
from ...config import settings
from ...llm import LLMError, get_provider
from ...orchestrator.store import enqueue_runner_request, get_runner_request
from ..schemas import (
    ChatAttachmentOut,
    ChatHistoryMessage,
    ChatQueuedResponse,
    ChatResponse,
    ChatStatusResponse,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("", response_model=ChatResponse | ChatQueuedResponse)
async def chat(
    message: str = Form(...),
    history: str = Form("[]"),
    ticker: str | None = Form(None),
    include_portfolio: bool = Form(True),
    files: list[UploadFile] | None = File(None),
) -> ChatResponse | ChatQueuedResponse:
    msg = message.strip()
    if not msg:
        raise HTTPException(status_code=422, detail="message is required")

    parsed_history = _parse_history(history)
    attachments = []
    attachment_blocks: list[str] = []
    for upload in files or []:
        content = await upload.read()
        try:
            att = parse_upload(upload.filename or "upload", upload.content_type, content)
        except ChatFileError as e:
            raise HTTPException(status_code=415, detail=str(e)) from e
        attachments.append(att)
        attachment_blocks.append(
            f"### {att.filename} ({att.parser}, {att.n_chars} chars)\n{att.text or '(no text extracted)'}"
        )

    attachment_context = "\n\n".join(attachment_blocks) or "(no uploaded files in this turn)"
    attachment_payload = _attachment_payload(attachments)
    clean_ticker = ticker.strip().upper() if ticker and ticker.strip() else None

    # Replit/dashboard deployments do not carry Codex auth. They write the chat
    # request to shared Turso and the trusted VPS/Hermes runner completes it with
    # the same Codex CLI stack used for thesis drift.
    if settings.is_dashboard_role():
        queued = enqueue_runner_request(
            command="chat_complete",
            ticker=clean_ticker,
            payload={
                "message": msg,
                "history": [m.model_dump() for m in parsed_history],
                "ticker": clean_ticker,
                "include_portfolio": include_portfolio,
                "attachment_context": attachment_context,
                "attachments": attachment_payload,
            },
        )
        return ChatQueuedResponse(**queued)

    # Run the blocking synchronous LLM call in a thread pool so it does not
    # stall the asyncio event loop (and avoid proxy-layer timeouts on long calls).
    try:
        result = await asyncio.to_thread(
            complete_chat_response,
            message=msg,
            history=parsed_history,
            ticker=clean_ticker,
            include_portfolio=include_portfolio,
            attachment_context=attachment_context,
            attachments=attachment_payload,
            provider_factory=get_provider,
        )
    except LLMError as e:
        if "No LLM provider" in str(e):
            raise HTTPException(status_code=503, detail=str(e)) from e
        raise HTTPException(status_code=502, detail=f"chat LLM failed: {str(e)[:300]}") from e

    return ChatResponse.model_validate(result)


@router.get("/{request_id}", response_model=ChatStatusResponse)
def chat_status(request_id: str) -> ChatStatusResponse:
    row = get_runner_request(request_id)
    if row is None or row.get("command") != "chat_complete":
        raise HTTPException(status_code=404, detail="chat request not found")

    result = None
    if row.get("status") == "succeeded":
        try:
            result = ChatResponse.model_validate(json.loads(row.get("summary_json") or "{}"))
        except Exception as e:  # noqa: BLE001 - corrupt queue rows should surface as failed status.
            raise HTTPException(status_code=502, detail=f"chat result unreadable: {str(e)[:200]}") from e
    return ChatStatusResponse(
        request_id=row["request_id"],
        status=row["status"],
        result=result,
        error=row.get("error"),
    )


def _parse_history(raw: str) -> list[ChatHistoryMessage]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[ChatHistoryMessage] = []
    for item in data[-16:]:
        try:
            msg = ChatHistoryMessage.model_validate(item)
        except Exception:
            continue
        if msg.content.strip():
            out.append(ChatHistoryMessage(role=msg.role, content=msg.content[:2_000]))
    return out


def _attachment_payload(attachments) -> list[dict]:
    return [
        ChatAttachmentOut(
            filename=a.filename,
            content_type=a.content_type,
            byte_size=a.byte_size,
            n_chars=a.n_chars,
            parser=a.parser,
        ).model_dump()
        for a in attachments
    ]
