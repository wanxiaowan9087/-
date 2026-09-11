from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from .models import DocumentRecord, DocumentType


TEXT_SUFFIXES = {".txt", ".md", ".markdown"}
PDF_SUFFIXES = {".pdf"}
EXCEL_SUFFIXES = {".xlsx"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | PDF_SUFFIXES | EXCEL_SUFFIXES


def parse_knowledge_payload(
    filename: str,
    payload: bytes,
    *,
    document_id: str,
    title: str,
    source: str,
) -> DocumentRecord:
    """Parse an uploaded file into the common RAG document interface.

    The parser deliberately returns page/sheet/row context in metadata so the
    existing chunker can keep citations useful after ingestion.
    """
    suffix = Path(filename).suffix.lower()
    if suffix in TEXT_SUFFIXES:
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("文本文件必须使用 UTF-8 编码") from error
        return DocumentRecord(document_id, title, source, _text_type(suffix), content=content)
    if suffix in PDF_SUFFIXES:
        return _parse_pdf(payload, document_id, title, source)
    if suffix in EXCEL_SUFFIXES:
        return _parse_excel(payload, document_id, title, source)
    raise ValueError(f"不支持的知识文件格式: {suffix or '无扩展名'}")


def _text_type(suffix: str) -> DocumentType:
    return DocumentType.MARKDOWN if suffix in {".md", ".markdown"} else DocumentType.TEXT


def _parse_pdf(payload: bytes, document_id: str, title: str, source: str) -> DocumentRecord:
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise RuntimeError("PDF 解析依赖 pypdf 未安装") from error
    try:
        reader = PdfReader(io.BytesIO(payload))
        pages = tuple((page.extract_text() or "").strip() for page in reader.pages)
    except Exception as error:
        raise ValueError("PDF 文件无法解析，可能是损坏或受密码保护") from error
    if not any(page for page in pages):
        raise ValueError("PDF 未提取到文本；扫描件需要先进行 OCR")
    return DocumentRecord(
        document_id,
        title,
        source,
        DocumentType.PDF,
        content="\n\n".join(page for page in pages if page),
        pages=pages,
        metadata={"page_count": len(pages)},
    )


def _parse_excel(payload: bytes, document_id: str, title: str, source: str) -> DocumentRecord:
    try:
        from openpyxl import load_workbook
    except ImportError as error:
        raise RuntimeError("Excel 解析依赖 openpyxl 未安装") from error
    try:
        workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    except Exception as error:
        raise ValueError("Excel 文件无法解析，可能是损坏或格式不受支持") from error
    lines: list[str] = []
    for sheet in workbook.worksheets:
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            continue
        headers = [_cell_text(value) or f"列{index + 1}" for index, value in enumerate(rows[0])]
        lines.append(f"工作表：{sheet.title}")
        for row_number, row in enumerate(rows[1:], 2):
            values = [_cell_text(value) for value in row]
            if not any(values):
                continue
            fields = [
                f"{headers[index]}={values[index]}"
                for index in range(min(len(headers), len(values)))
                if values[index]
            ]
            if fields:
                lines.append(f"行{row_number}：" + "；".join(fields))
    sheet_count = len(workbook.sheetnames)
    workbook.close()
    if not lines:
        raise ValueError("Excel 文件没有可索引的非空数据")
    return DocumentRecord(
        document_id,
        title,
        source,
        DocumentType.TABLE,
        content="\n".join(lines),
        metadata={"sheet_count": sheet_count},
    )


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
