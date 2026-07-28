from __future__ import annotations

import csv
import hashlib
import io
import re
from collections.abc import Callable
from typing import TypeAlias

from .models import Chunk, DocumentRecord, DocumentType, SourceLocation

ChunkMetadata: TypeAlias = dict[str, str | int]
ChunkPiece: TypeAlias = tuple[str, SourceLocation, ChunkMetadata]


def document_version(document: DocumentRecord) -> str:
    if document.version:
        return document.version
    payload = "\n".join(document.pages) if document.pages else document.content
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


class DocumentChunker:
    def __init__(
        self, *, text_chunk_size: int = 600, text_overlap: int = 80
    ) -> None:
        if text_chunk_size < 80:
            raise ValueError("text_chunk_size must be at least 80")
        if not 0 <= text_overlap < text_chunk_size:
            raise ValueError("text_overlap must be smaller than chunk size")
        self._chunk_size = text_chunk_size
        self._overlap = text_overlap

    def split(self, document: DocumentRecord) -> tuple[Chunk, ...]:
        version = document_version(document)
        builders: dict[DocumentType, Callable[[DocumentRecord], list[ChunkPiece]]] = {
            DocumentType.FAQ: self._faq,
            DocumentType.MARKDOWN: self._markdown,
            DocumentType.TEXT: self._text,
            DocumentType.PDF: self._pdf,
            DocumentType.TABLE: self._table,
        }
        pieces = builders[document.document_type](document)
        chunks: list[Chunk] = []
        for ordinal, (content, location, metadata) in enumerate(pieces, 1):
            cleaned = _clean(content)
            if not cleaned:
                continue
            chunks.append(
                Chunk(
                    document_id=document.document_id,
                    document_version=version,
                    chunk_id=(
                        f"{document.document_id}:{version}:{ordinal:04d}"
                    ),
                    title=document.title,
                    source=document.source,
                    content=cleaned,
                    document_type=document.document_type,
                    location=location,
                    metadata={**document.metadata, **metadata},
                )
            )
        if not chunks:
            raise ValueError("document produced no non-empty chunks")
        return tuple(chunks)

    def _faq(
        self, document: DocumentRecord
    ) -> list[ChunkPiece]:
        pattern = re.compile(
            r"(?ims)^\s*(?:Q|问题)\s*[:：]\s*(.+?)\s*$"
            r"\s*^\s*(?:A|答案)\s*[:：]\s*(.+?)"
            r"(?=^\s*(?:Q|问题)\s*[:：]|\Z)"
        )
        matches = pattern.findall(document.content)
        if not matches:
            return self._text(document)
        return [
            (
                f"问题：{question.strip()}\n答案：{answer.strip()}",
                SourceLocation(section=question.strip()[:120]),
                {"faq_question": question.strip()},
            )
            for question, answer in matches
        ]

    def _markdown(
        self, document: DocumentRecord
    ) -> list[ChunkPiece]:
        pieces: list[ChunkPiece] = []
        heading = "document"
        buffer: list[str] = []
        for line in document.content.splitlines():
            match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$", line)
            if match:
                if buffer:
                    pieces.extend(
                        self._recursive_pieces(
                            "\n".join(buffer),
                            SourceLocation(section=heading),
                            {"heading": heading},
                        )
                    )
                heading = match.group(2).strip()
                buffer = [line]
            else:
                buffer.append(line)
        if buffer:
            pieces.extend(
                self._recursive_pieces(
                    "\n".join(buffer),
                    SourceLocation(section=heading),
                    {"heading": heading},
                )
            )
        return pieces

    def _text(
        self, document: DocumentRecord
    ) -> list[ChunkPiece]:
        return self._recursive_pieces(
            document.content, SourceLocation(), {}
        )

    def _pdf(
        self, document: DocumentRecord
    ) -> list[ChunkPiece]:
        pages = document.pages or (document.content,)
        pieces: list[ChunkPiece] = []
        for page_number, page in enumerate(pages, 1):
            pieces.extend(
                self._recursive_pieces(
                    page,
                    SourceLocation(page=page_number),
                    {"page": page_number},
                )
            )
        return pieces

    def _table(
        self, document: DocumentRecord
    ) -> list[ChunkPiece]:
        rows = list(csv.DictReader(io.StringIO(document.content)))
        if not rows:
            return self._text(document)
        return [
            (
                "；".join(f"{key}={value}" for key, value in row.items()),
                SourceLocation(row=row_number),
                {"row": row_number},
            )
            for row_number, row in enumerate(rows, 1)
        ]

    def _recursive_pieces(
        self,
        content: str,
        location: SourceLocation,
        metadata: ChunkMetadata,
    ) -> list[ChunkPiece]:
        text = _clean(content)
        if len(text) <= self._chunk_size:
            return [(text, location, metadata)]
        pieces: list[ChunkPiece] = []
        start = 0
        while start < len(text):
            target = min(len(text), start + self._chunk_size)
            end = _nearest_break(text, start, target)
            pieces.append((text[start:end], location, dict(metadata)))
            if end >= len(text):
                break
            start = max(start + 1, end - self._overlap)
        return pieces


def _clean(content: str) -> str:
    return re.sub(r"[ \t]+", " ", content.replace("\x00", "")).strip()


def _nearest_break(text: str, start: int, target: int) -> int:
    if target >= len(text):
        return len(text)
    floor = start + max(40, (target - start) // 2)
    candidates = [
        text.rfind(separator, floor, target)
        for separator in ("\n\n", "\n", "。", "！", "？", ". ", " ")
    ]
    split_at = max(candidates)
    return target if split_at <= start else split_at + 1
