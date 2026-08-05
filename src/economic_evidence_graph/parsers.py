"""Deterministic document detection, security checks, and normalization."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from typing import Protocol

from defusedxml import ElementTree as SafeElementTree
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from economic_evidence_graph.domain import QualityReport, QuarantineReason
from economic_evidence_graph.exceptions import DocumentParseError


@dataclass(frozen=True, slots=True)
class ParsedUnit:
    """Parser output before it receives stable document identifiers."""

    ordinal: int
    section_path: tuple[str, ...]
    locator: str
    page_start: int | None
    page_end: int | None
    text: str


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Deterministic normalized parser output."""

    parser_name: str
    parser_version: str
    schema_version: str
    text: str
    units: tuple[ParsedUnit, ...]
    quality: QualityReport


class OcrEngine(Protocol):
    """Optional open-source OCR provider boundary."""

    def extract_pdf(self, content: bytes) -> ParsedDocument:
        """Extract a scanned PDF into the canonical parsed shape."""
        ...


class ParseFailure(DocumentParseError):
    """Parser rejection carrying structured quarantine behavior."""

    def __init__(self, reason: QuarantineReason, detail: str, *, retryable: bool = False) -> None:
        super().__init__(detail, context={"reason": reason.value})
        self.reason = reason
        self.retryable = retryable


class DocumentParserRouter:
    """Route safe bounded content to deterministic open-source parsers."""

    def __init__(self, *, maximum_bytes: int = 52_428_800, ocr: OcrEngine | None = None) -> None:
        if maximum_bytes < 1:
            raise ValueError("parser maximum bytes must be positive")
        self._maximum_bytes = maximum_bytes
        self._ocr = ocr

    def parse(self, content: bytes, *, media_type: str) -> ParsedDocument:
        """Detect and normalize one asset or raise a quarantine failure."""
        if len(content) > self._maximum_bytes:
            raise ParseFailure(QuarantineReason.SIZE_LIMIT, "asset exceeds parser byte limit")
        detected = _detect_media_type(content, media_type)
        if detected == "application/pdf":
            try:
                return _parse_pdf(content)
            except ParseFailure as error:
                if error.reason is QuarantineReason.OCR_REQUIRED and self._ocr is not None:
                    return self._ocr.extract_pdf(content)
                raise
        if detected in {"text/html", "application/xhtml+xml"}:
            return _parse_html(content)
        if detected in {"text/plain", "text/csv"}:
            return _parse_text(content)
        if detected in {"application/xml", "text/xml"}:
            return _parse_xml(content)
        if detected == "application/json":
            return _parse_json(content)
        raise ParseFailure(QuarantineReason.UNSUPPORTED_MEDIA_TYPE, f"unsupported media type: {detected}")


def _detect_media_type(content: bytes, declared: str) -> str:
    """Prefer safe signatures over untrusted Content-Type declarations."""
    prefix = content[:512].lstrip()
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if prefix.startswith((b"<!DOCTYPE html", b"<!doctype html", b"<html", b"<HTML")):
        return "text/html"
    normalized = declared.lower().split(";", 1)[0].strip()
    return normalized or "application/octet-stream"


def _parse_pdf(content: bytes) -> ParsedDocument:
    """Extract page-anchored text from a non-active PDF."""
    if any(marker in content for marker in (b"/JavaScript", b"/Launch", b"/EmbeddedFile")):
        raise ParseFailure(QuarantineReason.SECURITY_REJECTED, "PDF contains active or embedded content")
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise ParseFailure(QuarantineReason.ENCRYPTED, "PDF is encrypted")
        page_text = tuple(_normalize_text(page.extract_text() or "") for page in reader.pages)
    except ParseFailure:
        raise
    except (PdfReadError, ValueError, TypeError) as error:
        raise ParseFailure(QuarantineReason.MALFORMED, "PDF parser rejected malformed bytes") from error
    text = "\n\n".join(item for item in page_text if item)
    if not text or len(text) < max(40, len(page_text) * 10):
        raise ParseFailure(QuarantineReason.OCR_REQUIRED, "PDF has insufficient embedded text", retryable=True)
    units = tuple(
        ParsedUnit(index, ("Document",), f"page:{index + 1}", index + 1, index + 1, item)
        for index, item in enumerate(page_text)
        if item
    )
    density = len(text) / len(page_text) if page_text else None
    return ParsedDocument(
        "pypdf",
        "6",
        "eeg.normalized-document.v1",
        text,
        units,
        QualityReport("pdf_text", len(page_text), len(text), density, False, ()),
    )


def _parse_html(content: bytes) -> ParsedDocument:
    """Extract reading-order block text and heading paths from HTML."""
    parser = _BlockHtmlParser()
    try:
        parser.feed(content.decode("utf-8", errors="replace"))
        parser.close()
    except (ValueError, AssertionError) as error:
        raise ParseFailure(QuarantineReason.MALFORMED, "HTML parser rejected malformed markup") from error
    candidate_blocks = parser.primary_blocks if parser.has_primary_content_root else parser.blocks
    blocks: list[tuple[tuple[str, ...], str]] = []
    seen: set[str] = set()
    removed = 0
    for section, text in candidate_blocks:
        key = text.casefold()
        if not _is_substantive_html_text(text) or (key in seen and len(text) <= 200):
            removed += 1
            continue
        seen.add(key)
        blocks.append((section, text))
    units = tuple(
        ParsedUnit(index, section, f"html:block:{index}", None, None, text)
        for index, (section, text) in enumerate(blocks)
    )
    text = "\n\n".join(unit.text for unit in units)
    if not text:
        raise ParseFailure(QuarantineReason.EMPTY_TEXT, "HTML contains no readable block text")
    warnings: list[str] = []
    if all(unit.section_path == ("Document",) for unit in units):
        warnings.append("missing_heading_structure")
    if removed or parser.skipped_container_count:
        warnings.append("boilerplate_removed")
    return ParsedDocument(
        "stdlib_html",
        "2",
        "eeg.normalized-document.v1",
        text,
        units,
        QualityReport("html_blocks", None, len(text), None, False, tuple(warnings)),
    )


def _parse_text(content: bytes) -> ParsedDocument:
    """Normalize plain text into paragraph units."""
    text = _normalize_text(content.decode("utf-8", errors="replace"))
    paragraphs = tuple(item.strip() for item in re.split(r"\n\s*\n", text) if item.strip())
    if not paragraphs:
        raise ParseFailure(QuarantineReason.EMPTY_TEXT, "plain-text asset is empty")
    units = tuple(
        ParsedUnit(index, ("Document",), f"paragraph:{index}", None, None, item)
        for index, item in enumerate(paragraphs)
    )
    return ParsedDocument(
        "stdlib_text",
        "1",
        "eeg.normalized-document.v1",
        "\n\n".join(paragraphs),
        units,
        QualityReport("plain_text", None, len(text), None, False, ()),
    )


def _parse_xml(content: bytes) -> ParsedDocument:
    """Safely extract visible text from XML without entities or networking."""
    try:
        root = SafeElementTree.fromstring(content)
    except Exception as error:
        raise ParseFailure(QuarantineReason.MALFORMED, "XML parser rejected unsafe or malformed input") from error
    text = _normalize_text(" ".join(root.itertext()))
    return _parse_text(text.encode())


def _parse_json(content: bytes) -> ParsedDocument:
    """Render JSON scalars deterministically for source-record inspection."""
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ParseFailure(QuarantineReason.MALFORMED, "JSON parser rejected malformed input") from error
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    return _parse_text(text.encode())


def _normalize_text(value: str) -> str:
    """Normalize Unicode, whitespace, and line endings deterministically."""
    normalized = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in normalized.splitlines())
    return "\n".join(lines).strip()


class _BlockHtmlParser(HTMLParser):
    """Extract headings and block-level reading order while skipping active UI."""

    _BLOCKS = frozenset({"p", "li", "blockquote", "td", "th", "figcaption"})
    _PRIMARY = frozenset({"main", "article"})
    _SKIP = frozenset({"script", "style", "noscript", "svg", "nav", "form", "header", "footer", "aside", "menu"})
    _SKIP_ATTRIBUTE_TOKENS = frozenset(
        {
            "breadcrumb",
            "breadcrumbs",
            "cookie",
            "footer",
            "header",
            "masthead",
            "menu",
            "nav",
            "navbar",
            "navigation",
            "share",
            "social",
            "utility",
        }
    )
    _VOID = frozenset(
        {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._primary_depth = 0
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._headings: list[str] = []
        self.blocks: list[tuple[tuple[str, ...], str]] = []
        self.primary_blocks: list[tuple[tuple[str, ...], str]] = []
        self.skipped_container_count = 0
        self.has_primary_content_root = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Start a heading or readable block."""
        tag = tag.lower()
        if self._skip_depth:
            if tag not in self._VOID:
                self._skip_depth += 1
            return
        if tag in self._SKIP or _has_boilerplate_attributes(attrs, self._SKIP_ATTRIBUTE_TOKENS):
            self.skipped_container_count += 1
            if tag not in self._VOID:
                self._skip_depth = 1
            return
        if tag in self._PRIMARY:
            self.has_primary_content_root = True
            if self._primary_depth == 0:
                self._headings = []
            self._primary_depth += 1
        elif tag in self._BLOCKS or re.fullmatch(r"h[1-6]", tag):
            self._capture = tag
            self._buffer = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Ignore void elements without corrupting skipped-container depth."""
        if self._skip_depth:
            return
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in self._VOID:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        """Collect visible text."""
        if self._skip_depth == 0 and self._capture is not None:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        """Finish a heading or block."""
        tag = tag.lower()
        if self._skip_depth:
            self._skip_depth -= 1
            return
        if tag in self._PRIMARY and self._primary_depth:
            self._primary_depth -= 1
            return
        if tag != self._capture:
            return
        text = _normalize_text(" ".join(self._buffer))
        if re.fullmatch(r"h[1-6]", tag):
            level = int(tag[1])
            self._headings = self._headings[: level - 1]
            self._headings.append(text)
        elif text:
            block = (tuple(self._headings) or ("Document",), text)
            self.blocks.append(block)
            if self._primary_depth:
                self.primary_blocks.append(block)
        self._capture = None
        self._buffer = []


def _has_boilerplate_attributes(attrs: list[tuple[str, str | None]], tokens: frozenset[str]) -> bool:
    """Recognize conventional navigation and site-chrome containers."""
    values = " ".join(
        value for name, value in attrs if value is not None and name.casefold() in {"class", "id", "role", "aria-label"}
    )
    attribute_tokens = set(re.findall(r"[a-z]+", values.casefold()))
    return bool(attribute_tokens.intersection(tokens))


def _is_substantive_html_text(text: str) -> bool:
    """Reject headings and link labels that cannot stand alone as evidence."""
    words = re.findall(r"\b[\w'-]+\b", text, flags=re.UNICODE)
    has_sentence_boundary = re.search(r"[.!?]", text) is not None
    return len(text) >= 32 and len(words) >= 4 and (len(text) > 80 or has_sentence_boundary)
