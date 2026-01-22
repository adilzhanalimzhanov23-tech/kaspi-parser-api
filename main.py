import re
import os
import shutil
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, List, Dict

import pypdfium2 as pdf
from fastapi import FastAPI, UploadFile, File, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware

# =========================
# НАСТРОЙКИ ПРИЛОЖЕНИЯ
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
# РЕГУЛЯРНЫЕ ВЫРАЖЕНИЯ (FORTE & KASPI)
# =========================

# Игнорируем технические строки и доходы
IGNORE_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        "пополнение", "перевод самому себе", "депозит", 
        "со своего счета", "на свой счет", "вознаграждение"
    ]
]

# Универсальный поиск даты: DD.MM.YY или DD.MM.YYYY
DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{2,4})")

# Поиск суммы: ловит форматы "-1 500.00", "1500,00", "- 500"
# Обязательно ищет привязку к валюте ₸, T, KZT или "тенге"
AMOUNT_WITH_CURRENCY_RE = re.compile(
    r"(?P<amount>-?\s?\d[\d\s,.]*)\s*(?:₸|T|KZT|тенге)", 
    re.IGNORECASE
)

# Поиск MCC: 4 цифры подряд
MCC_RE = re.compile(r"\b(?P<mcc>\d{4})\b")

# =========================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================

def clean_amount(val_str: str) -> Decimal:
    """Превращает 'грязную' строку суммы в чистое число Decimal."""
    try:
        # Убираем всё, кроме цифр, точки, запятой и минуса
        cleaned = re.sub(r"[^\d,.-]", "", val_str)
        # Заменяем запятую на точку для стандарта Decimal
        if "," in cleaned and "." in cleaned:
            cleaned = cleaned.replace(",", "") # для формата 1,200.50
        else:
            cleaned = cleaned.replace(",", ".") # для формата 1200,50
        
        return Decimal(cleaned)
    except Exception:
        return Decimal("0")

def parse_line(line: str) -> Optional[Dict]:
    """Разбирает одну строку текста из выписки."""
    
    # 1. Ищем дату. Нет даты — нет транзакции.
    date_match = DATE_RE.search(line)
    if not date_match:
        return None

    # 2. Ищем сумму с валютой (основной признак транзакции в Forte/Kaspi)
    amount_match = AMOUNT_WITH_CURRENCY_RE.search(line)
    if not amount_match:
        return None

    try:
        raw_amount = amount_match.group("amount")
        amount_dec = clean_amount(raw_amount)

        # Нас интересуют только расходы (отрицательные суммы)
        if amount_dec >= 0:
            return None

        # 3. Ищем MCC (4 цифры), исключая саму дату
        mcc = None
        all_numbers = MCC_RE.findall(line)
        date_str = date_match.group(1)
        for num in all_numbers:
            if num not in date_str:
                mcc = num
                break

        return {
            "date": date_str,
            "amount": float(abs(amount_dec)), # Возвращаем положительное число для фронта
            "name": line.strip(),
            "mcc": mcc
        }
    except Exception as e:
        logger.error(f"Ошибка парсинга строки: {line} -> {e}")
        return None

# =========================
# API ЭНДПОИНТЫ
# =========================

@app.post("/analyze")
async def analyze_pdf(
    file: UploadFile = File(...), 
    x_api_key: Optional[str] = Header(None)
):
    # Проверка ключа доступа
    if x_api_key != API_KEY_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    temp_path = f"/tmp/{file.filename}"
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    transactions = []
    
    try:
        doc = pdf.PdfDocument(temp_path)
        
        for page in doc:
            text_page = page.get_textpage()
            text_content = text_page.get_text_range()
            
            for line in text_content.splitlines():
                line = line.strip()
                if not line:
                    continue

                # Пропускаем игнорируемые паттерны
                if any(p.search(line) for p in IGNORE_PATTERNS):
                    continue

                tx = parse_line(line)
                if tx:
                    transactions.append(tx)
                else:
                    # Логируем подозрительные строки, которые не распарсились
                    if any(curr in line for curr in ["₸", "KZT", "T"]):
                        logger.info(f"ПРОПУЩЕНА СТРОКА: {line}")

        if not transactions:
            logger.error("Транзакции не найдены в файле")
            raise HTTPException(
                status_code=422, 
                detail="Парсер не нашёл транзакций. Проверьте, что это выписка Forte или Kaspi."
            )

        return {"transactions": transactions}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Критическая ошибка парсинга")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)








