import csv
import re
import io
from pathlib import Path

try:
    import openpyxl
    XLSX_OK = True
except ImportError:
    XLSX_OK = False

try:
    import xlrd
    XLS_OK = True
except ImportError:
    XLS_OK = False

try:
    import fitz
    PDF_OK = True
except ImportError:
    PDF_OK = False


def _parse_lines(text: str) -> list[dict]:
    contacts = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"[,;\t]", line)
        if len(parts) >= 2:
            contacts.append({"name": parts[0].strip(), "number": parts[1].strip()})
        else:
            number = re.sub(r"\D", "", line)
            if len(number) >= 8:
                contacts.append({"name": "", "number": number})
    return contacts


def parse_csv(content: bytes) -> list[dict]:
    text = content.decode("utf-8-sig", errors="ignore")
    contacts = []
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if not row:
            continue
        first = row[0].lstrip("\ufeff").strip().lower()
        if first in ("nome", "name", "contato", "numero", "number", "telefone", "phone", "celular"):
            continue
        if len(row) >= 2:
            contacts.append({"name": row[0].strip(), "number": row[1].strip()})
        else:
            number = re.sub(r"\D", "", row[0])
            if len(number) >= 8:
                contacts.append({"name": "", "number": number})
    return contacts


def parse_txt(content: bytes) -> list[dict]:
    for encoding in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = content.decode("utf-8", errors="ignore")
    return _parse_lines(text)


def parse_xlsx(content: bytes) -> list[dict]:
    if not XLSX_OK:
        return []
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    contacts = []
    for row in ws.iter_rows(values_only=True):
        if not row or row[0] is None:
            continue
        name = str(row[0]).strip()
        if name.lower() in ("nome", "name", "contato", "numero", "number", "telefone", "phone", "celular"):
            continue
        if len(row) >= 2 and row[1] is not None:
            contacts.append({"name": name, "number": str(row[1]).strip()})
        else:
            number = re.sub(r"\D", "", name)
            if len(number) >= 8:
                contacts.append({"name": "", "number": number})
    wb.close()
    return contacts


def parse_pdf(content: bytes) -> list[dict]:
    if not PDF_OK:
        return []
    doc = fitz.open(stream=content, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    return _parse_lines(text)


def parse_xls(content: bytes) -> list[dict]:
    if not XLS_OK:
        return []
    wb = xlrd.open_workbook(file_contents=content)
    ws = wb.sheet_by_index(0)
    contacts = []
    for row_idx in range(ws.nrows):
        row = ws.row_values(row_idx)
        if not row or not row[0]:
            continue
        name = str(row[0]).strip()
        if name.lower() in ("nome", "name", "contato", "numero", "number", "telefone", "phone", "celular"):
            continue
        if len(row) >= 2 and row[1]:
            contacts.append({"name": name, "number": str(row[1]).strip()})
        else:
            number = re.sub(r"\D", "", name)
            if len(number) >= 8:
                contacts.append({"name": "", "number": number})
    return contacts


def parse_file(filename: str, content: bytes) -> list[dict]:
    ext = Path(filename).suffix.lower()
    parsers = {
        ".csv": parse_csv,
        ".txt": parse_txt,
        ".xlsx": parse_xlsx,
        ".xls": parse_xls,
        ".pdf": parse_pdf,
    }
    parser = parsers.get(ext)
    if not parser:
        raise ValueError(f"Formato nao suportado: {ext}")
    return parser(content)
