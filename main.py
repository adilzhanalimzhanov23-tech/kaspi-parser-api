import re
import os
import shutil
import base64
from io import BytesIO
from typing import List, Dict, Optional
from datetime import datetime
from collections import defaultdict
from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Form
from fastapi.middleware.cors import CORSMiddleware

# Попытка импорта движков PDF
try:
    import pypdfium2 as pdf
    PDF_ENGINE = "pypdfium2"
except ImportError:
    import pdfplumber
    PDF_ENGINE = "pdfplumber"

# Попытка импорта matplotlib для графиков
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.ticker import FuncFormatter
    CHARTS_ENABLED = True
except ImportError:
    CHARTS_ENABLED = False

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Секретный ключ для авторизации (настрой в Environment Variables на Render)
API_KEY_SECRET = os.getenv("MY_PARSER_SECRET", "fallback-secret-key")

# ==================== КАТЕГОРИИ И MCC ====================

MCC_CATEGORIES = {
    "Продукты": ["5411", "5422", "5441", "5451", "5462", "5499", "5921"],
    "Рестораны": ["5812", "5813", "5814", "5811"],
    "Транспорт": ["4111", "4112", "4121", "4131", "4784", "5541", "5542", "5983", "7512", "7523"],
    "Здоровье": ["5912", "8011", "8021", "8031", "8041", "8042", "8049", "8050", "8062", "8071", "8099"],
    "Развлечения": ["7832", "7841", "7911", "7922", "7929", "7932", "7933", "7941", "7991", "7992", "7993", "7994", "7995", "7996", "7997", "7998", "7999"],
    "Связь": ["4812", "4814", "4816", "4899"],
}

NAME_CATEGORIES = {
    "Продукты": ["magnum", "small", "арзан", "market", "маркет", "shop", "family shop"],
    "Рестораны": ["ресторан", "кафе", "coffee", "burger", "pizza", "food", "lanzhou", "sushi", "mcdonald", "kfc", "glovo", "wolt"],
    "Такси": ["uber", "yandex", "яндекс", "bolt", "indriver", "taxi"],
    "Транспорт": ["azs", "азс", "sinooil", "qazaq oil", "helios", "avtobys"],
    "Связь": ["beeline", "activ", "tele2", "altel", "izi"],
}

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def clean_amount(amount_str: str) -> float:
    """Парсинг суммы: убирает пробелы, фиксит запятые и точки"""
    if not amount_str:
        return 0.0
    # Очистка от спецсимволов и пробелов
    cleaned = amount_str.replace(" ", "").replace("\xa0", "").replace(",", ".")
    # Если точек больше одной (разделитель тысяч), убираем лишние
    if cleaned.count('.') > 1:
        parts = cleaned.split('.')
        cleaned = "".join(parts[:-1]) + "." + parts[-1]
    
    cleaned = re.sub(r"[^\d.\-+]", "", cleaned)
    try:
        return float(cleaned)
    except ValueError:
        return 0.0

def clean_name(text: str) -> str:
    """Чистит название магазина от банковских префиксов"""
    # Удаляем стандартные фразы Halyk 
    text = re.sub(r"^(Покупка|Оплата|Платеж|Перевод|Операция оплаты у коммерсанта|Поступление перевода)\s*", "", text, flags=re.IGNORECASE)
    # Удаляем MCC и лишние пробелы
    text = re.sub(r"MCC[:\s]*\d{4}", "", text)
    # Берем только первую часть до запятой (обычно там название)
    name = text.split(',')[0].strip()
    return name[:80] if name else "Транзакция"

def categorize_transaction(name: str, mcc: Optional[str], description: str) -> str:
    name_lower = name.lower()
    if mcc:
        for category, mcc_list in MCC_CATEGORIES.items():
            if mcc in mcc_list: return category
    
    for category, keywords in NAME_CATEGORIES.items():
        if any(kw in name_lower for kw in keywords): return category
    
    if "перевод" in name_lower or "p2p" in name_lower: return "Переводы"
    return "Прочее"

# ==================== ПАРСЕРЫ БАНКОВ ====================

def parse_halyk(text: str, lines: List[str]) -> List[Dict]:
    """Robust Halyk Bank parser (handles multiline descriptions)"""

    transactions = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # 1️⃣ Ищем строку, которая начинается с даты
        date_match = re.match(r"^(\d{2}\.\d{2}\.\d{4})\s+(\d{2}\.\d{2}\.\d{4})\s+(.+)$", line)
        if not date_match:
            i += 1
            continue

        date = date_match.group(1)
        description_part = date_match.group(3).strip()

        full_description = description_part
        amount = None
        income = 0.0
        expense = 0.0

        # 2️⃣ Собираем описание до тех пор, пока не найдём сумму
        j = i + 1
        while j < len(lines):

            # Если началась новая транзакция — останавливаемся
            if re.match(r"^\d{2}\.\d{2}\.\d{4}", lines[j]):
                break

            # Ищем сумму вида -1 625,00 или 2 100,00
            amount_match = re.search(r"(-?[\d\s]+[.,]\d{2})\s*KZT", lines[j])

            if amount_match:
                amount_str = amount_match.group(1)
                amount = clean_amount(amount_str)

                # В Halyk знак суммы уже правильный
                if amount < 0:
                    expense = amount
                else:
                    income = amount

                break

            # Иначе продолжаем собирать описание
            full_description += " " + lines[j].strip()
            j += 1

        # Если сумму нашли — создаём транзакцию
        if amount and amount != 0:
            name = full_description

            # Чистим стандартные фразы
            name = re.sub(r"Операция оплаты у\s*коммерсанта\s*", "", name, flags=re.IGNORECASE)
            name = re.sub(r"Поступление перевода\s*", "", name, flags=re.IGNORECASE)

            name = clean_name(name)

            if not should_ignore(name):
                transactions.append({
                    "date": date,
                    "amount": amount,
                    "name": name,
                    "mcc": None,
                    "description": "Покупка" if amount < 0 else "Пополнение"
                })

        i = j if j > i else i + 1

    return transactions

def parse_kaspi(text: str, lines: List[str]) -> List[Dict]:
    """Парсер для Kaspi Bank (новый формат с символом ₸)"""
    transactions = []
    kaspi_pattern = re.compile(r"^(\d{2}\.\d{2}\.\d{2})\s+([+-])\s+([\d\s]+,\d{2})\s*₸\s+(.+)$")
    
    for line in lines:
        match = kaspi_pattern.match(line.strip())
        if match:
            date = match.group(1)[:6] + "20" + match.group(1)[6:]
            sign = match.group(2)
            amount = clean_amount(match.group(3))
            if sign == "-": amount = -amount
            
            details = match.group(4).strip()
            transactions.append({
                "date": date,
                "amount": amount,
                "name": clean_name(details),
                "mcc": None,
                "description": details
            })
    return transactions

# ==================== АНАЛИТИКА ====================

def aggregate_transactions(transactions: List[Dict]) -> Dict:
    expenses = [t for t in transactions if t["amount"] < 0]
    incomes = [t for t in transactions if t["amount"] > 0]
    
    total_expense = sum(t["amount"] for t in expenses)
    total_income = sum(t["amount"] for t in incomes)
    
    # Группировка по категориям
    cat_summary = {}
    for t in expenses:
        cat = categorize_transaction(t["name"], t.get("mcc"), t.get("description", ""))
        if cat not in cat_summary: cat_summary[cat] = {"name": cat, "total": 0, "count": 0}
        cat_summary[cat]["total"] += t["amount"]
        cat_summary[cat]["count"] += 1

    # Группировка по мерчантам
    merchants = {}
    for t in expenses:
        name = t["name"]
        if name not in merchants: merchants[name] = {"name": name, "total": 0, "count": 0}
        merchants[name]["total"] += t["amount"]
        merchants[name]["count"] += 1

    return {
        "summary": {
            "total_expense": round(total_expense, 2),
            "total_income": round(total_income, 2),
            "balance": round(total_income + total_expense, 2),
            "transaction_count": len(transactions)
        },
        "categories": sorted(cat_summary.values(), key=lambda x: x["total"]),
        "merchants": sorted(merchants.values(), key=lambda x: x["total"])[:10] # Топ-10 трат
    }

def detect_bank(text: str) -> str:
    if "HSBKKZKX" in text or "Народный Банк" in text: return "halyk" [cite: 6, 46]
    if "Kaspi" in text: return "kaspi"
    return "halyk"

def extract_text(path: str) -> str:
    text = ""
    if PDF_ENGINE == "pypdfium2":
        doc = pdf.PdfDocument(path)
        for page in doc: text += page.get_textpage().get_text_range() + "\n"
        doc.close()
    else:
        with pdfplumber.open(path) as doc:
            for page in doc.pages: text += (page.extract_text() or "") + "\n"
    return text

# ==================== API ENDPOINTS ====================

@app.get("/")
async def root():
    return {"status": "online", "service": "Finpuls Parser API"}

@app.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    bank: Optional[str] = Form(None),
    x_api_key: Optional[str] = Header(None)
):
    if x_api_key != API_KEY_SECRET:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    
    temp_path = f"temp_{os.getpid()}_{file.filename}"
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        text = extract_text(temp_path)
        if not text.strip():
            raise HTTPException(status_code=400, detail="Empty PDF content")
            
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        target_bank = bank if bank else detect_bank(text)
        
        if target_bank == "halyk":
            transactions = parse_halyk(text, lines)
        elif target_bank == "kaspi":
            transactions = parse_kaspi(text, lines)
        else:
            transactions = []
            
        analytics = aggregate_transactions(transactions)
        
        return {
            "bank": target_bank,
            "transactions": transactions,
            **analytics
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

