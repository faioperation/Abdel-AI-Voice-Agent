from pathlib import Path
import io
import csv
from PyPDF2 import PdfReader

def get_mime_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    mime_map = {
        ".pdf": "application/pdf", ".txt": "text/plain", ".csv": "text/csv",
        ".tsv": "text/tab-separated-values", ".md": "text/markdown",
        ".json": "application/json", ".yaml": "application/x-yaml",
        ".yml": "application/x-yaml", ".xml": "application/xml",
        ".html": "text/html", ".htm": "text/html", ".css": "text/css",
        ".js": "text/javascript", ".ts": "application/typescript",
        ".log": "text/x-log", ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    return mime_map.get(ext, "text/plain")

def extract_text_from_bytes(content: bytes, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".txt":
        return content.decode("utf-8", errors="ignore")
    elif ext == ".pdf":
        try:
            reader = PdfReader(io.BytesIO(content))
            return "\n".join([page.extract_text() or "" for page in reader.pages])
        except:
            return ""
    elif ext == ".csv":
        try:
            text_content = content.decode("utf-8", errors="ignore")
            rows = list(csv.reader(io.StringIO(text_content)))
            return "\n".join([", ".join(row) for row in rows])
        except:
            return ""
    return ""