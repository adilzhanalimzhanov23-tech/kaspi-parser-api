import re
import os
import shutil
from decimal import Decimal
from typing import List, Dict, Optional
import pypdfium2 as pdf
from fastapi import FastAPI, UploadFile, File, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware

# !!! ВОТ ЭТО ИСКАЛ RENDER !!!
app = FastAPI()

# Разрешаем фронтенду подключаться
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY_SECRET = os.getenv("MY_PARSER_SECRET", "fallback-secret-key")

# =========================
# ТВОИ REGEX (БЕЗ ИЗМЕНЕНИЙ)
# =========================
DATE_RE = re.compile(r"\d{2}\.\d{2}\.\d{4}")
NEG_AMOUNT_RE = re.compile(r"-(\d[\d\s]*\.\d{2})\s*KZT")
MCC_RE = re.compile(r"MCC[:\s]*([\d\s]+)")
POPOLNENIE_RE = re.compile(r"Пополнение", re.IGNORECASE)

# =========================
# ТВОИ HELPERS (БЕЗ ИЗМЕНЕНИЙ)
# =========================
def clean_amount(val: str) -> Decimal:
    return Decimal(val.replace(" ", ""))

def extract_mcc(text: str) -> str | None:
    m = MCC_RE.search(text)
    if not m: return None
    return "".join(re.findall(r"\d", m.group(1)))

def extract_detail_name(detail: str) -> str:
    return detail.split(",")[0].strip()

# =========================
# ТВОЙ PARSER (БЕЗ ИЗМЕНЕНИЙ ЛОГИКИ)
# =========================
def parse_statement_pdf(path: str) -> List[Dict]:
    doc = pdf.PdfDocument(path)
    lines: List[str] = []
    for page in doc:
        textpage = page.get_textpage()
        text = textpage.get_text_range()
        lines.extend([l.strip() for l in text.splitlines() if l.strip()])

    transactions: List[Dict] = []
    current = None
    for line in lines:
        date_match = DATE_RE.search(line)
        amount_match = NEG_AMOUNT_RE.search(line)
        if date_match and amount_match:
            if current: transactions.append(current)
            if POPOLNENIE_RE.search(line):
                current = None
                continue
            amount = clean_amount(amount_match.group(1))
            current = {
                "date": date_match.group(),
                "amount": float(amount), # Перевел в float для JSON
                "name": "Покупка" if "Покупка" in line else "",
                "detail_name": "",
                "mcc": None,
                "_detail_raw": ""
            }
            continue
        if current:
            current["_detail_raw"] += " " + line
            mcc = extract_mcc(current["_detail_raw"])
            if mcc: current["mcc"] = mcc
            if not current["detail_name"]:
                current["detail_name"] = extract_detail_name(current["_detail_raw"])
    if current: transactions.append(current)
    for tx in transactions:
        # Для совместимости с твоим прошлым фронтом переименуем detail_name в name
        final_name = tx["detail_name"] if tx["detail_name"] else tx["name"]
        tx["name"] = final_name
        tx.pop("_detail_raw", None)
        tx.pop("detail_name", None)
        tx.pop("description", None)
    return transactions

# =========================
# ЭНДПОИНТ ДЛЯ RENDER
# =========================
@app.post("/analyze")
async def analyze(file: UploadFile = File(...), x_api_key: Optional[str] = Header(None)):
    if x_api_key != API_KEY_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    temp_path = f"/tmp/{file.filename}"
    with open(temp_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    
    try:
        data = parse_statement_pdf(temp_path)
        return {"transactions": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)

@app.get("/")
def health_check():
    return {"status": "working"}








