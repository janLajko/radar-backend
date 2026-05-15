from __future__ import annotations

import io
import logging

from pypdf import PdfReader

from radar_backend.sources.http_client import HttpClient

logger = logging.getLogger(__name__)


def download_and_parse(pdf_urls: list[str], http: HttpClient) -> str:
    """Download and extract text from one or more PDF URLs.

    - Single PDF failure: logs a warning and continues.
    - All PDFs failed: raises ``RuntimeError``.

    Returns extracted text from all successful PDFs joined by ``\\n\\n---\\n\\n``.
    """
    parts: list[str] = []

    for url in pdf_urls:
        try:
            resp = http.get(url)
            reader = PdfReader(io.BytesIO(resp.content))
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n".join(pages).strip()
            if text:
                parts.append(text)
        except Exception as exc:
            logger.warning("pdf download/parse failed url=%s: %s", url, exc)

    if not parts:
        raise RuntimeError(f"all PDFs failed to download or parse: {pdf_urls}")

    return "\n\n---\n\n".join(parts)
