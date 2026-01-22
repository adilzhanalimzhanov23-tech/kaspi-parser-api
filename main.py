import re
import os
import shutil
import logging
from decimal import Decimal, InvalidOperation
from typing import Optional, List, Dict

import pypdfium2 as pdf
from fastapi import FastAPI, UploadFile, File, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware

# =========================
# APP
# =========================

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY_SECRET = os.getenv("MY_PARSER_SECRET", "fallback-secret-key")

# =========================
# LOGGING
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("bank_pdf_parser")

# =========================
# REGEX & CONSTANTS
# =========================

IGNORE_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        "пополнение",
        "перевод самому себе",
        "депозит",
        "kaspi депозит",
        "гкп",
        "акимат",
        "со своего счета",
        "на свой счет",
    ]
]

KASPI_LINE_RE = re.compile(
    r"(?P<date>\d{2}\.\d{2}\.\d{4})\s+"
    r"(?P<amount>-?\d[\d\s,.]*)\s*[₸T]\s+"
    r"(?P<operation>Покупка|Перевод|Платеж|Другое)\s+"
    r"(?P<detail>.+)"
)

DATE_RE = re.compile(r"\d{2}\.\d{2}\.\d{4}")
AMOUNT_RE = re.compile(r"-?\d[\d\s,.]+")
MCC_RE = re.compile(r"\bMCC[:\s]?(?P<mcc>\d{4})\b")

# =========================
# HELPERS
# =========================

def clean_amount(val: str) -> Decimal:
    try:
        return Decimal(
            val.replace(" ", "")
               .replace("\u00a0", "")
               .replace(",", ".")
               .replace("₸", "")
               .replace("KZT", "")
               .strip()
        )
    except InvalidOperation:
        raise ValueError(f"Invalid amount value: {val}")


def should_ignore(text: str) -> bool:
    return any(p.search(text) for p in IGNORE_PATTERNS)


def parse_line(line: str) -> Optional[Dict]:
    # ---------- Kaspi classic ----------
    m = KASPI_LINE_RE.search(line)
    if m:
        amount = clean_amount(m.group("amount"))
        if amount >= 0:
            return None

        return {
            "date": m.group("date"),
            "amount": abs(amount),
            "name": m.group("detail").strip(),
            "mcc": None
        }

    # ---------- Табличные PDF ----------
    date = DATE_RE.search(line)
    amount = AMOUNT_RE.search(line)

    if date and amount:
        try:
            value = clean_amount(amount.group())
        except Exception:
            return None

        if value >= 0:
            return None

        mcc = MCC_RE.search(line)

        return {
            "date": date.group(),
            "amount": abs(value),
            "name": line.strip(),
            "mcc": mcc.group("mcc") if mcc else None
        }

    return None

# =========================
# API
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

    transactions: List[Dict] = []

    try:
        doc = pdf.PdfDocument(temp_path)

        for page in doc:
            textpage = page.get_textpage()
            text = textpage.get_text_range()

            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue

                if should_ignore(line):
                    continue

                tx = parse_line(line)
                if tx:
                    transactions.append(tx)
                else:
                    if "₸" in line and DATE_RE.search(line):
                        logger.warning(f"UNPARSED LINE: {line}")

        return {
            "transactions": transactions
        }

    except Exception as e:
        logger.exception("PDF parsing failed")
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

# =========================
# LOCAL RUN
# =========================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)







