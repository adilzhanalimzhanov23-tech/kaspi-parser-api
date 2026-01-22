import re
import os
import shutil
import logging
from decimal import Decimal
from typing import Optional, List, Dict

import pypdfium2 as pdf
from fastapi import FastAPI, UploadFile, File, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware

# =========================
# APP CONFIG
# =========================

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY_SECRET = os.getenv("MY_PARSER_SECRET", "fallback-secret-key")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("bank_pdf_parser")

# =========================
# REGEX DEFINITIONS
# =========================

IGNORE_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        "пополнение", "перевод самому себе", "депозит", 
        "со своего счета", "на свой счет", "вознаграждение"
    ]
]

DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{2,4})")
AMOUNT_WITH_CURRENCY_RE = re.compile(
    r"(?P<amount>-?\s?\d[\d\s,.]*)\s*(?:₸|T|KZT|тенге)", 
    re.IGNORECASE
)
MCC_RE = re.compile(r"\bMCC[:\s]?(?P<mcc>\d{4})\b")

# =========================
# HELPERS
# =========================

def clean_amount(val_str: str) -> Decimal:
    """Нормализует строку суммы в число."""
    try:
        cleaned = re.sub(r"[^\d,.-]", "", val_str)
        if "," in cleaned and "." in cleaned:
            cleaned = cleaned.replace(",", "")
        else:
            cleaned = cleaned.replace(",", ".")
        return Decimal(cleaned)
    except Exception:
        return Decimal("0")

def parse_line(line: str) -> Optional[Dict]:
    """Извлекает данные и очищает имя мерчанта от мусора."""
    
    # 1. Поиск даты
    date_match = DATE_RE.search(line)
    if not date_match:
        return None
    date_str = date_match.group(1)

    # 2. Поиск суммы и валюты
    amount_match = AMOUNT_WITH_CURRENCY_RE.search(line)
    if not amount_match:
        return None

    try:
        full_amount_str = amount_match.group(0) # например, "-590.00 KZT"
        amount_dec = clean_amount(amount_match.group("amount"))

        # Берем только расходы
        if amount_dec >= 0:
            return None

        # 3. Поиск MCC
        mcc_match = MCC_RE.search(line)
        mcc = mcc_match.group("mcc") if mcc_match else None

        # --- ОЧИСТКА ИМЕНИ (NAME) ---
        clean_name = line
        
        # Удаляем дату, полную подстроку суммы и валюты
        clean_name = clean_name.replace(date_str, "")
        clean_name = clean_name.replace(full_amount_str, "")
        
        # Удаляем упоминание MCC
        if mcc:
            clean_name = re.sub(rf"MCC[:\s]*{mcc}", "", clean_name)
            clean_name = clean_name.replace(mcc, "")

        # Удаляем банковский шум и ключевые слова
        noise = ["Покупка", "Платеж", "Перевод", "Retail", "KZT", "₸", "Тенге", "Jusan Bank"]
        for word in noise:
            clean_name = re.sub(rf"\b{word}\b", "", clean_name, flags=re.IGNORECASE)
        
        # Финальная чистка лишних знаков препинания и пробелов
        clean_name = re.sub(r"^\W+|\W+$", "", clean_name) # Удаляем символы в начале и конце
        clean_name = re.sub(r"\s+", " ", clean_name).strip()

        return {
            "date": date_str,
            "amount": float(abs(amount_dec)),
            "name": clean_name if clean_name else "Неизвестный мерчант",
            "mcc": mcc
        }
    except Exception as e:
        logger.error(f"Error parsing line: {e}")
        return None

# =========================
# API ROUTES
# =========================

@app.post("/analyze")
async def analyze_pdf(
    file: UploadFile = File(...), 
    x_api_key: Optional[str] = Header(None)
):
    if x_api_key != API_KEY_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    temp_path = f"/tmp/{file.filename}"
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    transactions = []
    try:
        doc = pdf.PdfDocument(temp_path)
        for page in doc:
            text = page.get_textpage().get_text_range()
            for line in text.splitlines():
                line = line.strip()
                if not line or any(p.search(line) for p in IGNORE_PATTERNS):
                    continue
                
                tx = parse_line(line)
                if tx:
                    transactions.append(tx)
                elif any(curr in line for curr in ["₸", "KZT", "T"]):
                    logger.info(f"SKIPPED LINE: {line}")

        if not transactions:
            raise HTTPException(status_code=422, detail="Транзакции не найдены.")

        return {"transactions": transactions}

    except Exception as e:
        logger.exception("Parse error")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)








