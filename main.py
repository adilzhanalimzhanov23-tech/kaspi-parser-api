"""
Multi-Bank Statement Parser API
Supports: Kaspi, Halyk, Freedom, CenterCredit, Alatau, Forte

Deploy on Render. Compatible with existing frontend.
"""

import re
import os
import shutil
from typing import List, Dict, Optional
from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Form
from fastapi.middleware.cors import CORSMiddleware

try:
    import pypdfium2 as pdf
    PDF_ENGINE = "pypdfium2"
except ImportError:
    import pdfplumber
    PDF_ENGINE = "pdfplumber"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY_SECRET = os.getenv("MY_PARSER_SECRET", "fallback-secret-key")

# ==================== CATEGORIES ====================

DEFAULT_CATEGORIES = {
    "groceries": "🛒 Продукты",
    "transport": "🚗 Транспорт",
    "food-out": "🍔 Еда вне дома",
    "shopping": "🛍️ Шопинг",
    "housing": "🏠 Жилье",
    "entertainment": "🎬 Досуг",
    "education": "📚 Учёба",
    "health": "💊 Здоровье",
    "finance": "💰 Финансы",
    "transfers": "↔️ Переводы",
    "other": "📦 Прочее",
}

# MCC to Category mapping (simplified version)
MCC_MAPPING = {
    # Продукты
    "5411": "groceries", "5422": "groceries", "5441": "groceries", "5451": "groceries",
    "5462": "groceries", "5499": "groceries", "5921": "groceries",
    # Транспорт
    "4111": "transport", "4112": "transport", "4121": "transport", "4131": "transport",
    "4511": "transport", "5172": "transport", "5541": "transport", "5542": "transport",
    "5983": "transport", "7511": "transport", "7512": "transport", "7523": "transport",
    "7531": "transport", "7534": "transport", "7538": "transport", "7542": "transport",
    # Еда вне дома
    "5811": "food-out", "5812": "food-out", "5813": "food-out", "5814": "food-out",
    # Шопинг
    "5262": "shopping", "5311": "shopping", "5331": "shopping", "5399": "shopping",
    "5611": "shopping", "5621": "shopping", "5631": "shopping", "5641": "shopping",
    "5651": "shopping", "5661": "shopping", "5691": "shopping", "5699": "shopping",
    "5712": "shopping", "5732": "shopping", "5733": "shopping", "5734": "shopping",
    "5735": "shopping", "5942": "shopping", "5944": "shopping", "5945": "shopping",
    "5946": "shopping", "5947": "shopping", "5948": "shopping", "5949": "shopping",
    # Жилье
    "4814": "housing", "4816": "housing", "4899": "housing", "4900": "housing",
    "5200": "housing", "5211": "housing", "5231": "housing", "5251": "housing",
    # Досуг
    "7011": "entertainment", "7832": "entertainment", "7841": "entertainment",
    "7911": "entertainment", "7922": "entertainment", "7929": "entertainment",
    "7932": "entertainment", "7933": "entertainment", "7941": "entertainment",
    "7991": "entertainment", "7994": "entertainment", "7996": "entertainment",
    "7997": "entertainment", "7999": "entertainment",
    # Учёба
    "5111": "education", "5192": "education", "5942": "education",
    "8211": "education", "8220": "education", "8241": "education", "8299": "education",
    # Здоровье
    "5122": "health", "5912": "health", "8011": "health", "8021": "health",
    "8031": "health", "8041": "health", "8042": "health", "8043": "health",
    "8049": "health", "8062": "health", "8071": "health", "8099": "health",
    # Финансы
    "6010": "finance", "6011": "finance", "6012": "finance", "6051": "finance",
    "6211": "finance", "6300": "finance",
}

# Name-based category detection
NAME_PATTERNS = {
    "transport": ["uber", "yandex", "такси", "taxi", "bolt", "indrive", "onay", "автобус", "bus", "sinooil", "qazaq oil", "helios", "азс", "заправ"],
    "food-out": ["mcdonalds", "kfc", "burger", "starbucks", "coffee", "кофе", "ресторан", "кафе", "пицца", "pizza", "sushi", "суши", "wolt", "glovo", "chocofood"],
    "groceries": ["magnum", "small", "магазин", "market", "маркет", "продукт", "arbuz", "metro", "супермаркет", "гастроном"],
    "shopping": ["wildberries", "ozon", "aliexpress", "amazon", "kaspi магазин", "technodom", "технодом", "sulpak", "mechta", "мечта"],
    "entertainment": ["кино", "cinema", "netflix", "spotify", "youtube", "театр", "концерт", "netflix", "кинотеатр"],
    "health": ["аптека", "pharmacy", "euroaptek", "биосфера", "медицин", "клиника", "hospital", "dental", "стоматолог"],
    "education": ["книг", "book", "курс", "course", "school", "university", "обучени"],
    "housing": ["коммунал", "электричество", "газ", "вода", "интернет", "beeline", "kcell", "tele2", "актив", "altel"],
    "finance": ["кредит", "credit", "loan", "страхов", "insurance", "банк"],
}


def detect_category(name: str, mcc: Optional[str] = None) -> Optional[str]:
    """Auto-detect category from MCC code or merchant name"""
    # First try MCC
    if mcc and mcc in MCC_MAPPING:
        return MCC_MAPPING[mcc]
    
    # Then try name patterns
    name_lower = name.lower()
    for category, patterns in NAME_PATTERNS.items():
        for pattern in patterns:
            if pattern in name_lower:
                return category
    
    return None

# ==================== BANK DETECTION ====================

BANK_SIGNATURES = {
    "forte": [r"ForteBank", r"Forte", r"IRTYKZKA", r"forte\.kz"],  # Check FIRST - Forte PDFs contain "Halyk Bank" in details
    "kaspi": [r"Kaspi\s*(Bank|Gold|Pay)", r"kaspi\.kz", r"CASPKZKA", r"Kaspi Gold"],
    "alatau": [r"Alatau\s*City\s*Bank", r"TSESKZKA", r"alataucitybank\.kz"],
    "halyk": [r"АО.*Народный Банк", r"HSBKKZKX", r"halykbank\.kz"],  # More specific to avoid false match
    "freedom": [r"Freedom\s*Bank", r"Фридом", r"KSNVKZKA", r"bankffin"],
    "centrecredit": [r"ЦентрКредит", r"CenterCredit", r"KCJBKZKX", r"bcc\.kz"],
    "jusan": [r"Jusan\s*Bank", r"Жусан"],
}

IGNORE_PATTERNS = [
    "С Kaspi Депозита", "На Kaspi Депозит", "На Kaspi депозит",
    "На свой Счет в Kaspi Pay", "Со своего Счета в Kaspi Pay",
    "Перевод самому себе", "Пополнение Kaspi Gold",
    "Перевод между своими счетами", "Пополнение счета",
]

# ==================== КАТЕГОРИИ ====================

# MCC коды по категориям
MCC_CATEGORIES = {
    # Продукты питания
    "Продукты": ["5411", "5422", "5441", "5451", "5462", "5499", "5921"],
    # Рестораны и кафе  
    "Рестораны": ["5812", "5813", "5814", "5811"],
    # Транспорт
    "Транспорт": ["4111", "4112", "4121", "4131", "4784", "5541", "5542", "5983", "7512", "7523"],
    # Такси и каршеринг
    "Такси": ["4121", "4789"],
    # Одежда и обувь
    "Одежда": ["5611", "5621", "5631", "5641", "5651", "5661", "5681", "5691", "5699"],
    # Здоровье и аптеки
    "Здоровье": ["5912", "8011", "8021", "8031", "8041", "8042", "8049", "8050", "8062", "8071", "8099"],
    # Развлечения
    "Развлечения": ["7832", "7841", "7911", "7922", "7929", "7932", "7933", "7941", "7991", "7992", "7993", "7994", "7995", "7996", "7997", "7998", "7999"],
    # Красота и уход
    "Красота": ["7230", "7297", "7298"],
    # Электроника
    "Электроника": ["5722", "5732", "5733", "5734", "5735", "5945", "5946"],
    # Дом и ремонт
    "Дом": ["5200", "5211", "5231", "5251", "5261", "5712", "5713", "5714", "5718", "5719", "5722"],
    # Образование
    "Образование": ["8211", "8220", "8241", "8244", "8249", "8299"],
    # Связь и интернет
    "Связь": ["4812", "4813", "4814", "4815", "4816", "4821", "4899"],
    # Путешествия
    "Путешествия": ["3000", "3001", "4011", "4111", "4112", "4511", "4722", "7011", "7012", "7032", "7033"],
    # Подписки и сервисы
    "Подписки": ["5815", "5816", "5817", "5818", "5968"],
    # Супермаркеты
    "Супермаркеты": ["5311", "5331", "5399"],
    # Переводы
    "Переводы": [],
    # Прочее
    "Прочее": [],
}

# Ключевые слова для категоризации по названию
NAME_CATEGORIES = {
    "Продукты": ["magnum", "small", "арзан", "продукт", "market", "маркет", "bazaar", "базар", "alina", "гастроном"],
    "Рестораны": ["ресторан", "кафе", "coffee", "кофе", "burger", "бургер", "pizza", "пицца", "food", "lanzhou", "суши", "sushi", "mcdonald", "kfc", "hardee"],
    "Такси": ["uber", "yandex", "яндекс", "bolt", "indriver", "такси", "taxi", "индрайвер"],
    "Транспорт": ["azs", "азс", "sinooil", "petrol", "бензин", "gas station", "qazaq gas", "avtobys", "автобус"],
    "Одежда": ["zara", "h&m", "bershka", "lc waikiki", "adidas", "nike", "puma", "бутик", "одежда", "обувь"],
    "Здоровье": ["аптека", "pharmacy", "pharma", "dentist", "стоматолог", "клиника", "hospital", "медицин"],
    "Развлечения": ["cinema", "кино", "kino.kz", "ticketon", "театр", "концерт", "парк"],
    "Красота": ["салон", "salon", "beauty", "барбер", "barber", "spa", "маникюр", "парикмахер"],
    "Электроника": ["technodom", "технодом", "sulpak", "сулпак", "mechta", "мечта", "alser", "альсер", "apple", "samsung"],
    "Дом": ["kaspi", "home", "мебель", "ikea", "hoff", "leroy", "строй"],
    "Образование": ["university", "университет", "school", "школа", "курс", "course", "edu", "обучение"],
    "Связь": ["beeline", "activ", "tele2", "altel", "казахтелеком", "telecom", "izi"],
    "Путешествия": ["hotel", "отель", "booking", "aviata", "chocotravel", "travel", "airport", "аэропорт", "railway", "вокзал"],
    "Подписки": ["netflix", "spotify", "youtube", "google", "apple", "openai", "chatgpt", "claude", "subscription", "подписка", "premium"],
    "Супермаркеты": ["magnum", "small", "metro", "grossmart", "anvar"],
    "Переводы": ["перевод", "на карту", "p2p"],
    "Маркетплейсы": ["ozon", "wildberries", "kaspi", "alibaba", "aliexpress", "amazon"],
}


def categorize_transaction(name: str, mcc: Optional[str], description: str) -> str:
    """Определить категорию транзакции по MCC или названию"""
    name_lower = name.lower()
    desc_lower = description.lower() if description else ""
    
    # 1. Сначала проверяем MCC код
    if mcc:
        for category, mcc_list in MCC_CATEGORIES.items():
            if mcc in mcc_list:
                return category
    
    # 2. Проверяем по типу операции
    if "перевод" in desc_lower or "перевод" in name_lower:
        return "Переводы"
    
    if "пополнение" in desc_lower:
        return "Доходы"
    
    # 3. Проверяем по ключевым словам в названии
    for category, keywords in NAME_CATEGORIES.items():
        for keyword in keywords:
            if keyword in name_lower:
                return category
    
    return "Прочее"


def aggregate_transactions(transactions: List[Dict]) -> Dict:
    """Агрегировать транзакции: сводка, группы по мерчантам, по категориям, по дням"""
    
    if not transactions:
        return {
            "summary": {
                "total_expense": 0,
                "total_income": 0,
                "balance": 0,
                "transaction_count": 0,
                "expense_count": 0,
                "income_count": 0,
            },
            "merchants": [],
            "expense_sources": [],
            "income_sources": [],
            "categories": [],
            "daily": [],
        }
    
    # Разделяем на расходы и доходы
    expenses = [t for t in transactions if t["amount"] < 0]
    incomes = [t for t in transactions if t["amount"] > 0]
    
    total_expense = sum(t["amount"] for t in expenses)
    total_income = sum(t["amount"] for t in incomes)
    
    # Группировка расходов по мерчантам
    expense_merchants = {}
    for t in expenses:
        name = t["name"]
        if name not in expense_merchants:
            expense_merchants[name] = {
                "name": name,
                "total": 0,
                "count": 0,
                "category": categorize_transaction(name, t.get("mcc"), t.get("description", "")),
                "transactions": []
            }
        expense_merchants[name]["total"] += t["amount"]
        expense_merchants[name]["count"] += 1
        expense_merchants[name]["transactions"].append({
            "date": t["date"],
            "amount": t["amount"]
        })
    
    # Группировка доходов по источникам
    income_sources = {}
    for t in incomes:
        name = t["name"]
        if name not in income_sources:
            income_sources[name] = {
                "name": name,
                "total": 0,
                "count": 0,
                "category": "Доходы",
                "transactions": []
            }
        income_sources[name]["total"] += t["amount"]
        income_sources[name]["count"] += 1
        income_sources[name]["transactions"].append({
            "date": t["date"],
            "amount": t["amount"]
        })
    
    # Группировка по категориям (только расходы)
    categories = {}
    for t in expenses:
        cat = categorize_transaction(t["name"], t.get("mcc"), t.get("description", ""))
        if cat not in categories:
            categories[cat] = {"name": cat, "total": 0, "count": 0}
        categories[cat]["total"] += t["amount"]
        categories[cat]["count"] += 1
    
    # Группировка по дням
    daily = {}
    for t in transactions:
        date = t["date"]
        if date not in daily:
            daily[date] = {"date": date, "expense": 0, "income": 0}
        if t["amount"] < 0:
            daily[date]["expense"] += t["amount"]
        else:
            daily[date]["income"] += t["amount"]
    
    # Сортировка
    expense_list = sorted(expense_merchants.values(), key=lambda x: x["total"])  # От большего расхода к меньшему
    income_list = sorted(income_sources.values(), key=lambda x: x["total"], reverse=True)
    category_list = sorted(categories.values(), key=lambda x: x["total"])
    daily_list = sorted(daily.values(), key=lambda x: x["date"])
    
    return {
        "summary": {
            "total_expense": round(total_expense, 2),
            "total_income": round(total_income, 2),
            "balance": round(total_income + total_expense, 2),
            "transaction_count": len(transactions),
            "expense_count": len(expenses),
            "income_count": len(incomes),
        },
        "merchants": expense_list,  # Для совместимости с фронтендом
        "expense_sources": expense_list,
        "income_sources": income_list,
        "categories": category_list,
        "daily": daily_list,
    }


def detect_bank(text: str) -> str:
    """Auto-detect bank from PDF text content"""
    for bank, patterns in BANK_SIGNATURES.items():
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return bank
    return "kaspi"  # Default


def extract_text(path: str) -> str:
    """Extract text from PDF"""
    text = ""
    if PDF_ENGINE == "pypdfium2":
        doc = pdf.PdfDocument(path)
        try:
            for page in doc:
                t = page.get_textpage().get_text_range()
                if t:
                    text += t + "\n"
        finally:
            doc.close()
    else:
        with pdfplumber.open(path) as doc:
            for page in doc.pages:
                text += (page.extract_text() or "") + "\n"
    return text


def should_ignore(detail: str) -> bool:
    """Check if transaction should be ignored (internal transfers)"""
    detail_lower = detail.lower()
    return any(p.lower() in detail_lower for p in IGNORE_PATTERNS)


def clean_name(text: str) -> str:
    """Clean merchant name"""
    if "Получатель:" in text:
        card = re.search(r"(\d{4,6}\*+\d{4})|(\*+\d{4})", text)
        return f"Перевод {card.group() if card else ''}".strip()
    
    name = re.sub(r"^(Покупка|Оплата|Платеж|Перевод)\s*", "", text, flags=re.IGNORECASE).strip()
    name = name.split(',')[0].strip()
    name = re.sub(r"\s+KZ$", "", name)
    name = re.sub(r"\s+(ALMATY|ASTANA|SHYMKENT|NUR-SULTAN).*$", "", name, flags=re.IGNORECASE)
    return name[:80] if name else "Транзакция"


def clean_amount(amount_str: str) -> float:
    """Parse amount string to float"""
    if not amount_str:
        return 0.0
    cleaned = amount_str.replace(" ", "").replace(",", ".").replace("−", "-")
    cleaned = re.sub(r"[^\d.\-+]", "", cleaned)
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


# ==================== BANK PARSERS ====================

def parse_kaspi(text: str, lines: List[str]) -> List[Dict]:
    """Parse Kaspi Bank statement - new format with ₸ symbol"""
    transactions = []
    
    # Kaspi format: DD.MM.YY [+-] 12 600,00 ₸ Операция Детали
    # Note: amount has space as thousands separator, comma as decimal
    kaspi_pattern = re.compile(
        r"^(\d{2}\.\d{2}\.\d{2})\s+"          # Date DD.MM.YY
        r"([+-])\s+"                           # Sign with space
        r"([\d\s]+,\d{2})\s*₸\s+"              # Amount (space separated thousands, comma decimal)
        r"(Перевод|Покупка|Пополнение|Снятие|Разное)\s+"  # Operation
        r"(.+)$"                               # Details
    )
    
    for line in lines:
        match = kaspi_pattern.match(line.strip())
        if match:
            date = match.group(1)
            sign = match.group(2)
            amount_str = match.group(3).replace(" ", "").replace(",", ".")
            operation = match.group(4)
            details = match.group(5).strip()
            
            try:
                amount = float(amount_str)
                if sign == "-":
                    amount = -amount
            except:
                continue
            
            name = clean_name(details)
            
            if should_ignore(name):
                continue
            
            # Convert date from DD.MM.YY to DD.MM.20YY
            date = date[:6] + "20" + date[6:]
            
            transactions.append({
                "date": date,
                "amount": amount,
                "name": name,
                "mcc": None,
                "description": operation
            })
    
    # Fallback: old format with KZT (for older statements)
    if not transactions:
        for i, line in enumerate(lines):
            date_m = re.search(r"(\d{2}\.\d{2}\.\d{4})", line)
            amount_m = re.search(r"(-?\d[\d\s]*\.\d{2})\s*KZT", line)
            
            if date_m and amount_m:
                if "Пополнение" in line and "Покупка" not in line:
                    continue
                
                is_transfer = "Перевод" in line
                current_amount = float(amount_m.group(1).replace(" ", ""))
                
                details_block = []
                for j in range(i + 1, min(i + 5, len(lines))):
                    if re.search(r"\d{2}\.\d{2}\.\d{4}", lines[j]):
                        break
                    details_block.append(lines[j])
                
                full_details = " ".join(details_block)
                
                mcc = None
                if not is_transfer:
                    search_zone = line + " " + full_details
                    collapsed = re.sub(r"\s+", "", search_zone)
                    mcc_match = re.search(r"MCC:?(\d{4})", collapsed, re.IGNORECASE)
                    if mcc_match:
                        mcc = mcc_match.group(1)
                
                if "," in line or "Получатель" in line:
                    name = clean_name(line)
                elif details_block:
                    name = clean_name(details_block[0])
                else:
                    name = "Транзакция"
                
                if should_ignore(name):
                    continue
                
                transactions.append({
                    "date": date_m.group(1),
                    "amount": current_amount,
                    "name": name,
                    "mcc": mcc,
                    "description": "Перевод" if is_transfer else "Покупка"
                })
    
    return transactions


def parse_halyk(text: str, lines: List[str]) -> List[Dict]:
    """Parse Halyk Bank statement"""
    transactions = []
    
    # Pattern: DD.MM.YYYY DD.MM.YYYY Description Amount KZT Income Expense
    pattern = re.compile(
        r"(\d{2}\.\d{2}\.\d{4})\s+\d{2}\.\d{2}\.\d{4}\s+"
        r"(.+?)\s+"
        r"(-?[\d\s,]+[.,]\d{2})\s+KZT\s+"
        r"([\d\s,]+[.,]\d{2})\s+"
        r"(-?[\d\s,]+[.,]\d{2})"
    )
    
    for match in pattern.finditer(text):
        date = match.group(1)
        detail = match.group(2).strip()
        income = clean_amount(match.group(4))
        expense = clean_amount(match.group(5))
        
        # Clean up description
        detail = re.sub(r"Операция оплаты у коммерсанта\s*", "", detail)
        detail = re.sub(r"Поступление на счет\s*", "", detail)
        name = clean_name(detail)
        
        if should_ignore(name):
            continue
        
        # MCC from description
        mcc = None
        mcc_match = re.search(r"MCC[:\s]*(\d{4})", detail)
        if mcc_match:
            mcc = mcc_match.group(1)
        
        if expense > 0:
            transactions.append({
                "date": date,
                "amount": -abs(expense),  # Negative for expense
                "name": name,
                "mcc": mcc,
                "description": "Покупка"
            })
        elif income > 0:
            transactions.append({
                "date": date,
                "amount": income,  # Positive for income
                "name": name,
                "mcc": mcc,
                "description": "Пополнение"
            })
    
    # Fallback: simpler pattern
    if not transactions:
        simple_pattern = re.compile(
            r"(\d{2}\.\d{2}\.\d{4})\s+(.+?)\s+(-?[\d\s,]+[.,]\d{2})\s*KZT"
        )
        for match in simple_pattern.finditer(text):
            date = match.group(1)
            detail = match.group(2).strip()
            amount = clean_amount(match.group(3))
            
            name = clean_name(detail)
            if should_ignore(name) or amount == 0:
                continue
            
            transactions.append({
                "date": date,
                "amount": amount,
                "name": name,
                "mcc": None,
                "description": "Покупка" if amount < 0 else "Пополнение"
            })
    
    return transactions


def parse_freedom(text: str, lines: List[str]) -> List[Dict]:
    """Parse Freedom Bank statement"""
    transactions = []
    
    # Freedom format from PDF:
    # DD.MM.YYYY -1,234.00 ₸ KZT Операция Детали
    # Example: 12.01.2026 -2,200.00 ₸ KZT Перевод Перевод с карты на карту
    
    freedom_pattern = re.compile(
        r"(\d{2}\.\d{2}\.\d{4})\s+"              # Date
        r"([+-]?[\d,]+[.,]\d{2})\s*₸?\s*"        # Amount
        r"(?:KZT|USD|EUR)?\s*"                    # Currency (optional)
        r"(Пополнение|Перевод|Покупка|Снятие|Платеж|Другое)?\s*"  # Operation
        r"(.+)?$"                                  # Details
    )
    
    for line in lines:
        match = freedom_pattern.match(line.strip())
        if match:
            date = match.group(1)
            amount_str = match.group(2).replace(",", "").replace(" ", "")
            operation = match.group(3) or ""
            detail = match.group(4) or ""
            
            try:
                amount = float(amount_str)
            except:
                continue
            
            if amount == 0:
                continue
            
            name = clean_name(f"{detail}".strip()) if detail else (operation or "Транзакция")
            
            if should_ignore(name):
                continue
            
            transactions.append({
                "date": date,
                "amount": amount,
                "name": name,
                "mcc": None,
                "description": operation or ("Покупка" if amount < 0 else "Пополнение")
            })
    
    # Second pass: look for table-style format
    if not transactions:
        # Look for lines that start with date
        date_pattern = re.compile(r"^(\d{2}\.\d{2}\.\d{4})")
        amount_pattern = re.compile(r"([+-]?[\d,]+[.,]\d{2})\s*₸")
        
        for i, line in enumerate(lines):
            date_match = date_pattern.match(line)
            if date_match:
                date = date_match.group(1)
                amount_match = amount_pattern.search(line)
                
                if amount_match:
                    amount_str = amount_match.group(1).replace(",", "")
                    try:
                        amount = float(amount_str)
                    except:
                        continue
                    
                    if amount == 0:
                        continue
                    
                    # Get details after amount
                    rest = line[amount_match.end():].strip()
                    # Remove currency marker
                    rest = re.sub(r"^(KZT|USD|EUR)\s*", "", rest)
                    
                    # Try to extract operation and details
                    op_match = re.match(r"(Пополнение|Перевод|Покупка|Снятие|Платеж|Другое)\s*(.*)", rest)
                    if op_match:
                        operation = op_match.group(1)
                        detail = op_match.group(2).strip()
                    else:
                        operation = "Покупка" if amount < 0 else "Пополнение"
                        detail = rest
                    
                    name = clean_name(detail) if detail else operation
                    
                    if should_ignore(name):
                        continue
                    
                    transactions.append({
                        "date": date,
                        "amount": amount,
                        "name": name,
                        "mcc": None,
                        "description": operation
                    })
    
    return transactions


def parse_centrecredit(text: str, lines: List[str]) -> List[Dict]:
    """Parse CenterCredit (BCC) Bank statement - multi-line format"""
    transactions = []
    
    # CenterCredit format: dates on one line, description spans multiple lines, amount at end
    # Pattern: YYYY-MM-DD YYYY-MM-DD Описание ... Amount KZT
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Look for line starting with two dates
        date_match = re.match(r"^(\d{4}-\d{2}-\d{2})\s+\d{4}-\d{2}-\d{2}\s+(.+)$", line)
        
        if date_match:
            date_iso = date_match.group(1)
            operation_start = date_match.group(2).strip()
            
            # Collect the full description and find the amount
            full_desc = operation_start
            amount = None
            
            # Look ahead for amount (negative or positive with KZT)
            for j in range(i+1, min(i+20, len(lines))):
                check_line = lines[j]
                
                # Check if this is a new transaction (starts with date)
                if re.match(r"^\d{4}-\d{2}-\d{2}", check_line):
                    break
                
                # Look for amount pattern: -70 493.00 or 70 493.00 followed by KZT
                amt_match = re.search(r"(-?[\d\s]+[.,]\d{2})\s*$", check_line)
                if amt_match and j+1 < len(lines) and "KZT" in lines[j+1]:
                    amount_str = amt_match.group(1).replace(" ", "").replace(",", ".")
                    try:
                        amount = float(amount_str)
                    except:
                        pass
                    break
                
                full_desc += " " + check_line
            
            if amount is not None and amount != 0:
                # Convert date from YYYY-MM-DD to DD.MM.YYYY
                date_parts = date_iso.split("-")
                date = f"{date_parts[2]}.{date_parts[1]}.{date_parts[0]}"
                
                # Determine operation type from description
                if "Пополнение" in full_desc:
                    operation = "Пополнение"
                    name = "Школа-лицей №48" if "Школа-лицей" in full_desc else clean_name(full_desc)
                elif "Перевод" in full_desc:
                    operation = "Перевод"
                    name = "Перевод"
                else:
                    operation = "Покупка"
                    name = clean_name(full_desc)
                
                if not should_ignore(name):
                    transactions.append({
                        "date": date,
                        "amount": amount,
                        "name": name,
                        "mcc": None,
                        "description": operation
                    })
        i += 1
    
    return transactions


def parse_alatau(text: str, lines: List[str]) -> List[Dict]:
    """Parse Alatau City Bank statement - multi-line format"""
    transactions = []
    
    # Alatau format is multi-line:
    # Line 1: DD.MM.YYYY
    # Line 2: HH:MM:SS
    # Line 3: Операция Детали
    # Line 4+: Референс и прочее
    # Last line: Amount KZT Income Expense
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Look for date line
        date_match = re.match(r"^(\d{2}\.\d{2}\.\d{4})$", line.strip())
        if date_match and i + 3 < len(lines):
            date = date_match.group(1)
            
            # Next line should be time
            time_match = re.match(r"^(\d{2}:\d{2}:\d{2})$", lines[i+1].strip())
            if time_match:
                # Next line has operation and details
                op_line = lines[i+2].strip()
                op_match = re.match(r"^(Покупка|Пополнение|Перевод|Снятие|Комиссия|Прочие)\s+(.+)?$", op_line)
                
                if op_match:
                    operation = op_match.group(1)
                    detail = op_match.group(2) or ""
                    
                    # Find the amount line (contains KZT and two numbers at end)
                    for j in range(i+3, min(i+8, len(lines))):
                        amount_match = re.search(r"([\d\s]+(?:\.\d+)?)\s+KZT\s+(\d+)\s+([\d.]+)$", lines[j])
                        if amount_match:
                            income = clean_amount(amount_match.group(2))
                            expense = clean_amount(amount_match.group(3))
                            
                            # Clean detail
                            detail = re.sub(r"Референс:.*", "", detail).strip()
                            detail = re.sub(r"Код авторизации:.*", "", detail).strip()
                            name = clean_name(detail) if detail else operation
                            
                            if not should_ignore(name) and name:
                                if expense > 0:
                                    transactions.append({
                                        "date": date,
                                        "amount": -abs(expense),
                                        "name": name,
                                        "mcc": None,
                                        "description": operation
                                    })
                                elif income > 0:
                                    transactions.append({
                                        "date": date,
                                        "amount": income,
                                        "name": name,
                                        "mcc": None,
                                        "description": operation
                                    })
                            i = j
                            break
        i += 1
    
    return transactions


def parse_forte(text: str, lines: List[str]) -> List[Dict]:
    """Parse Forte Bank statement - format with MCC at end of details"""
    transactions = []
    
    # Forte format (may span multiple lines):
    # DD.MM.YYYY -Amount KZT Описание DETAILS, MCC: XXXX
    # or DD.MM.YYYY Amount KZT Пополнение счета DETAILS
    
    # First, join lines that are continuation of previous (don't start with date)
    joined_lines = []
    current_line = ""
    
    for line in lines:
        # Skip header lines
        if any(skip in line for skip in ["Дата Сумма", "Детализация выписки", "заблокированная сумма"]):
            continue
            
        # Check if line starts with date pattern
        if re.match(r"^\d{2}\.\d{2}\.\d{4}", line.strip()):
            if current_line:
                joined_lines.append(current_line)
            current_line = line.strip()
        elif current_line:
            # Continuation of previous line - join with space
            current_line += " " + line.strip()
    
    if current_line:
        joined_lines.append(current_line)
    
    # Pattern for Forte transactions
    forte_pattern = re.compile(
        r"^(\d{2}\.\d{2}\.\d{4})\s+"           # Date
        r"(-?[\d\s]+[.,]\d{2})\s*KZT\s+"       # Amount
        r"(Покупка|Пополнение счета|Перевод|Платеж)\s*"  # Operation type
        r"(.+)$"                                 # Details (including MCC)
    )
    
    for line in joined_lines:
        match = forte_pattern.match(line)
        if match:
            date = match.group(1)
            amount_str = match.group(2).replace(" ", "").replace(",", ".")
            operation = match.group(3)
            details = match.group(4).strip()
            
            try:
                amount = float(amount_str)
            except:
                continue
            
            # Skip zero amounts
            if amount == 0:
                continue
            
            # Extract MCC from details - handle split MCC like "MCC: 41 21" -> "4121"
            mcc = None
            # First try normal MCC pattern
            mcc_match = re.search(r"MCC[:\s]*(\d{4})", details)
            if mcc_match:
                mcc = mcc_match.group(1)
            else:
                # Try split MCC pattern (e.g., "MCC: 41 21" or "MCC: 5 814")
                mcc_split = re.search(r"MCC[:\s]*(\d{1,2})\s+(\d{2,3})", details)
                if mcc_split:
                    mcc = mcc_split.group(1) + mcc_split.group(2)
                    if len(mcc) == 4:
                        pass  # Valid MCC
                    else:
                        mcc = None
            
            # Remove MCC from details for cleaner name
            details_clean = re.sub(r",?\s*MCC[:\s]*\d+\s*\d*\s*$", "", details).strip()
            
            # Extract merchant name from details
            # Format: "MERCHANT,LOCATION,CITY,KZ, Bank Name" or just "MERCHANT"
            name_parts = details_clean.split(",")
            if name_parts:
                name = name_parts[0].strip()
                # Clean up common prefixes
                name = re.sub(r"^(KASPI_QR_RETAILER|Получатель:)\s*", "", name)
                name = name.strip()
            else:
                name = details_clean
            
            # Handle transfers
            if operation == "Перевод" and "Получатель:" in details:
                card_match = re.search(r"(\d{6}\*+\d{4})", details)
                name = f"Перевод {card_match.group(1) if card_match else ''}"
            
            # Handle bus payments
            if "Avtobys" in details:
                name = "Avtobys (Проезд)"
                mcc = mcc or "4111"  # Public transport MCC
            
            if not name or should_ignore(name):
                continue
            
            transactions.append({
                "date": date,
                "amount": amount,
                "name": name[:80],
                "mcc": mcc,
                "description": operation
            })
    
    return transactions


PARSERS = {
    "kaspi": parse_kaspi,
    "halyk": parse_halyk,
    "freedom": parse_freedom,
    "centrecredit": parse_centrecredit,
    "alatau": parse_alatau,
    "forte": parse_forte,
}


# ==================== API ENDPOINTS ====================

@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "Multi-Bank Parser API",
        "version": "2.0.0",
        "supported_banks": list(PARSERS.keys())
    }


@app.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    bank: Optional[str] = Form(None),
    x_api_key: Optional[str] = Header(None)
):
    """
    Analyze bank statement PDF.
    
    - file: PDF file
    - bank: Bank type (kaspi, halyk, freedom, centrecredit, alatau, forte) or auto-detect
    - x_api_key: API key for authentication
    
    Returns full analytics:
    - bank: detected bank name
    - summary: totals (expense, income, balance, counts)
    - transactions: raw transaction list
    - merchants: expense sources grouped
    - income_sources: income sources grouped  
    - categories: expenses by category
    - daily: daily expense/income breakdown
    """
    if x_api_key != API_KEY_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    temp_path = f"temp_{os.getpid()}_{file.filename}"
    
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Extract text
        text = extract_text(temp_path)
        if not text.strip():
            raise HTTPException(status_code=400, detail="Could not extract text from PDF")
        
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        
        # Detect or use specified bank
        detected_bank = bank if bank and bank in PARSERS else detect_bank(text)
        print(f"[Parser] Detected bank: {detected_bank}")
        
        # Get parser
        parser = PARSERS.get(detected_bank, parse_kaspi)
        
        # Parse transactions
        transactions = parser(text, lines)
        
        print(f"[Parser] Found {len(transactions)} transactions")
        
        # Aggregate and categorize
        analytics = aggregate_transactions(transactions)
        
        # Return full response
        return {
            "bank": detected_bank,
            "transactions": transactions,
            **analytics
        }
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Parser] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
