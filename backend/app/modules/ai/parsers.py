"""按扩展名分发的文档文本解析器注册表。

所有解析器签名统一为 ``Callable[[bytes], str]``。解析失败时抛 ``DocumentParseError``。
错误信息只含格式名与原因、绝不包含文件内容。
"""

from __future__ import annotations

from collections.abc import Callable
from io import BytesIO
from zipfile import BadZipFile

from docx import Document as load_docx
from docx.opc.exceptions import PackageNotFoundError as DocxPackageNotFoundError
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from pptx import Presentation as load_pptx
from pptx.exc import PackageNotFoundError as PptxPackageNotFoundError
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from .exceptions import DocumentParseError

MAX_EXTRACTED_TEXT_LENGTH = 20_000
MAX_PDF_PAGES = 200
MAX_XLSX_ROWS = 2000
TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "gb18030")
LEGACY_FORMAT_UPGRADES = {"doc": "docx", "xls": "xlsx", "ppt": "pptx"}

Parser = Callable[[bytes], str]


def parse_plain_text(content: bytes) -> str:
    for encoding in TEXT_ENCODINGS:
        try:
            return content.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="ignore").strip()


def parse_pdf(content: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(content), strict=False)
        parts: list[str] = []
        total_length = 0
        for index, page in enumerate(reader.pages):
            if index >= MAX_PDF_PAGES or total_length >= MAX_EXTRACTED_TEXT_LENGTH:
                break
            text = (page.extract_text() or "").strip()
            if text:
                parts.append(text)
                total_length += len(text)
        return "\n".join(parts)
    except (PyPdfError, ValueError, KeyError, OSError) as exc:
        raise DocumentParseError(
            format="pdf",
            reason=f"文件损坏或内容无法读取 ({type(exc).__name__})",
        ) from exc


def parse_docx(content: bytes) -> str:
    try:
        document = load_docx(BytesIO(content))
        parts: list[str] = [
            paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()
        ]
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    parts.append("\t".join(cells))
        return "\n".join(parts)
    except (DocxPackageNotFoundError, BadZipFile, KeyError, ValueError) as exc:
        raise DocumentParseError(
            format="docx",
            reason=f"文件损坏或内容无法读取 ({type(exc).__name__})",
        ) from exc


def parse_xlsx(content: bytes) -> str:
    try:
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
        try:
            parts: list[str] = []
            total_rows = 0
            for worksheet in workbook.worksheets:
                parts.append(f"[{worksheet.title}]")
                for row in worksheet.iter_rows(values_only=True):
                    if total_rows >= MAX_XLSX_ROWS:
                        break
                    cells = [
                        str(value).strip()
                        for value in row
                        if value is not None and str(value).strip()
                    ]
                    if cells:
                        parts.append("\t".join(cells))
                    total_rows += 1
                if total_rows >= MAX_XLSX_ROWS:
                    break
            return "\n".join(parts)
        finally:
            workbook.close()
    except (BadZipFile, InvalidFileException, KeyError, ValueError, OSError) as exc:
        raise DocumentParseError(
            format="xlsx",
            reason=f"文件损坏或内容无法读取 ({type(exc).__name__})",
        ) from exc


def parse_pptx(content: bytes) -> str:
    try:
        presentation = load_pptx(BytesIO(content))
        parts: list[str] = []
        for slide in presentation.slides:
            for shape in slide.shapes:
                if not shape.has_text_frame:
                    continue
                text = shape.text_frame.text.strip()
                if text:
                    parts.append(text)
        return "\n".join(parts)
    except (PptxPackageNotFoundError, BadZipFile, KeyError, ValueError) as exc:
        raise DocumentParseError(
            format="pptx",
            reason=f"文件损坏或内容无法读取 ({type(exc).__name__})",
        ) from exc


PARSER_REGISTRY: dict[str, Parser] = {
    "txt": parse_plain_text,
    "md": parse_plain_text,
    "csv": parse_plain_text,
    "pdf": parse_pdf,
    "docx": parse_docx,
    "xlsx": parse_xlsx,
    "pptx": parse_pptx,
}


def extract_text_from_bytes(content: bytes, extension: str) -> str:
    normalized = extension.lower().lstrip(".")
    if normalized in LEGACY_FORMAT_UPGRADES:
        upgrade = LEGACY_FORMAT_UPGRADES[normalized]
        raise DocumentParseError(
            format=normalized,
            reason=f"不支持的旧格式。请转存为 {upgrade} 后重新上传",
        )
    parser = PARSER_REGISTRY.get(normalized)
    if parser is None:
        return ""
    return parser(content)[:MAX_EXTRACTED_TEXT_LENGTH]
