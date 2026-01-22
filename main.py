import re
import os
import shutil
from decimal import Decimal
from typing import List, Dict, Optional
import pypdfium2 as pdf
from fastapi import FastAPI, UploadFile, File, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

API_KEY_SECRET = os.getenv("MY_PARSER_SECRET", "fallback-secret-key")

# =========================
# ТОЧНЫЕ РЕГУЛЯРКИ
# =========================
DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})")
# Сумма с обязательным KZT
AMOUNT_RE = re.compile(r"(-?\d[\d\s]*\.\d{2})\s*KZT")
MCC_RE = re.compile(r"MCC[:\s]*(\d[\d\s]*)", re.IGNORECASE)

def clean_name(text: str) -> str:
    """Оставляет только название до первой запятой и убирает мусор"""
    # Если это перевод по номеру карты/счета
    if "Получатель:" in text:
        card = re.search(r"(\d{4,6}\*+\d{4})|(\*+\d{4})", text)
        return f"Перевод {card.group() if card else ''}".strip()
    
    # Убираем Покупка/Оплата/Платеж в начале
    name = re.sub(r"^(Покупка|Оплата|Платеж|Перевод)\s*", "", text, flags=re.IGNORECASE).strip()
    # Берем строго до первой запятой
    name = name.split(',')[0].strip()
    return name

def extract_mcc_from_block(text: str) -> Optional[str]:
    """Находит MCC и склеивает цифры, если они разбиты"""
    m = MCC_RE.search(text)
    if m:
        digits = "".join(re.findall(r"\d", m.group(1)))
        return digits[:4] if len(digits) >= 2 else None
    return None

def parse_statement_pdf(path: str) -> List[Dict]:
    doc = pdf.PdfDocument(path)
    try:
        lines = []
        for page in doc:
            t = page.get_textpage().get_text_range()
            if t:
                lines.extend([l.strip() for l in t.splitlines() if l.strip()])

        transactions = []
        
        for i, line in enumerate(lines):
            date_m = DATE_RE.search(line)
            amount_m = AMOUNT_RE.search(line)

            # Если нашли начало транзакции
            if date_m and amount_m:
                if "Пополнение" in line: continue

                # Название магазина в этой выписке часто идет в СЛЕДУЮЩЕЙ строке
                # Мы берем окно из 3 строк для анализа деталей
                context_lines = lines[i:i+4]
                full_context = " ".join(context_lines)

                # 1. Ищем название (оно обычно идет после слова Покупка или в следующей строке)
                name = "Транзакция"
                for l in context_lines:
                    # Если строка содержит детализацию (обычно там есть запятые или MCC)
                    if "," in l or "MCC" in l or "Получатель" in l:
                        name = clean_name(l)
                        break
                
                # 2. Ищем MCC
                mcc = extract_mcc_from_block(full_context)

                transactions.append({
                    "date": date_m.group(1),
                    "amount": float(amount_m.group(1).replace(" ", "")),
                    "name": name,
                    "mcc": mcc,
                    "description": "Перевод" if "Перевод" in full_context else "Покупка"
                })

        return transactions
    finally:
        doc.close()

@app.post("/analyze")
async def analyze(file: UploadFile = File(...), x_api_key: Optional[str] = Header(None)):
    if x_api_key != API_KEY_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    temp_path = f"temp_{os.getpid()}_{file.filename}"
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        data = parse_statement_pdf(temp_path)
        return {"transactions": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)








