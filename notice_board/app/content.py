from __future__ import annotations

import json
import re
from typing import Any, Callable

ALLOWED_NODE_TYPES = {
    "doc",
    "paragraph",
    "text",
    "heading",
    "bulletList",
    "orderedList",
    "listItem",
    "blockquote",
    "horizontalRule",
    "hardBreak",
    "codeBlock",
    "pdfAttachment",
}
ALLOWED_MARK_TYPES = {"bold", "italic", "strike", "code"}
MAX_DOC_NODES = 5000
MAX_TEXT_CHARS = 50000


def legacy_text_doc(text: str | None) -> dict[str, Any]:
    value = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    blocks: list[dict[str, Any]] = []
    for line in value.split("\n"):
        if line:
            blocks.append({"type": "paragraph", "content": [{"type": "text", "text": line}]})
        else:
            blocks.append({"type": "paragraph"})
    if not blocks:
        blocks = [{"type": "paragraph"}]
    return {"type": "doc", "content": blocks}


def load_doc(value: str | dict[str, Any] | None, fallback_text: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value:
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return legacy_text_doc(fallback_text)


def dump_doc(doc: dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"))


def attachment_ids(doc: dict[str, Any]) -> set[str]:
    out: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "pdfAttachment":
                file_id = (node.get("attrs") or {}).get("fileId")
                if isinstance(file_id, str) and file_id:
                    out.add(file_id)
            for child in node.get("content") or []:
                walk(child)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(doc)
    return out


def sanitize_doc(
    raw: str | dict[str, Any] | None,
    attachment_lookup: Callable[[str], dict[str, Any] | None] | None = None,
    fallback_text: str = "",
) -> tuple[dict[str, Any], str]:
    """Validate a small Tiptap schema and return (canonical JSON doc, plain-text projection).

    Attachment metadata is canonicalized from the database. Client-supplied page counts,
    file names, and inline eligibility are never trusted.
    """
    doc = load_doc(raw, fallback_text)
    counter = {"nodes": 0, "chars": 0}

    def clean_marks(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        marks: list[dict[str, Any]] = []
        seen: set[str] = set()
        for mark in value:
            if not isinstance(mark, dict):
                continue
            kind = mark.get("type")
            if kind in ALLOWED_MARK_TYPES and kind not in seen:
                marks.append({"type": kind})
                seen.add(kind)
        return marks

    def clean_node(node: Any) -> dict[str, Any] | None:
        if not isinstance(node, dict):
            return None
        kind = node.get("type")
        if kind not in ALLOWED_NODE_TYPES:
            return None
        counter["nodes"] += 1
        if counter["nodes"] > MAX_DOC_NODES:
            raise ValueError("公告內容過於複雜")

        if kind == "text":
            text = node.get("text")
            if not isinstance(text, str):
                return None
            counter["chars"] += len(text)
            if counter["chars"] > MAX_TEXT_CHARS:
                raise ValueError("公告文字超過 50,000 字")
            result: dict[str, Any] = {"type": "text", "text": text}
            marks = clean_marks(node.get("marks"))
            if marks:
                result["marks"] = marks
            return result

        if kind == "pdfAttachment":
            attrs = node.get("attrs") or {}
            file_id = attrs.get("fileId")
            if not isinstance(file_id, str) or not file_id or attachment_lookup is None:
                return None
            meta = attachment_lookup(file_id)
            if not meta:
                raise ValueError("公告包含無法使用的 PDF 附件")
            pages = int(meta["page_count"])
            requested_mode = attrs.get("displayMode")
            display_mode = "inline" if requested_mode == "inline" and pages <= 10 else "attachment"
            return {
                "type": "pdfAttachment",
                "attrs": {
                    "fileId": file_id,
                    "fileName": str(meta["original_name"]),
                    "pageCount": pages,
                    "sizeBytes": int(meta["size_bytes"]),
                    "displayMode": display_mode,
                },
            }

        result = {"type": kind}
        if kind == "heading":
            try:
                level = int((node.get("attrs") or {}).get("level", 2))
            except (TypeError, ValueError):
                level = 2
            result["attrs"] = {"level": level if level in (1, 2, 3) else 2}
        children = []
        for child in node.get("content") or []:
            cleaned = clean_node(child)
            if cleaned:
                children.append(cleaned)
        if children:
            result["content"] = children
        return result

    cleaned = clean_node(doc)
    if not cleaned or cleaned.get("type") != "doc":
        cleaned = legacy_text_doc(fallback_text)
    text = doc_to_text(cleaned)
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("公告文字超過 50,000 字")
    return cleaned, text


def doc_to_text(doc: dict[str, Any]) -> str:
    """Stable plain-text projection used by the personal version diff."""
    block_types = {"paragraph", "heading", "blockquote", "listItem", "codeBlock"}

    def render(node: Any) -> str:
        if not isinstance(node, dict):
            return ""
        kind = node.get("type")
        if kind == "text":
            return str(node.get("text") or "")
        if kind == "hardBreak":
            return "\n"
        if kind == "horizontalRule":
            return "\n―――\n"
        if kind == "pdfAttachment":
            attrs = node.get("attrs") or {}
            name = str(attrs.get("fileName") or "PDF")
            pages = attrs.get("pageCount")
            mode = "直接顯示" if attrs.get("displayMode") == "inline" else "附檔"
            page_text = f"，{pages}頁" if pages else ""
            return f"\n[PDF：{name}{page_text}，{mode}]\n"
        parts = [render(child) for child in node.get("content") or []]
        value = "".join(parts)
        if kind in block_types:
            value += "\n"
        elif kind in {"bulletList", "orderedList"} and value and not value.endswith("\n"):
            value += "\n"
        return value

    text = render(doc)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
