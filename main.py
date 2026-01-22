import re
import os
import shutil
from typing import List, Dict, Optional
import pypdfium2 as pdf
from fastapi import FastAPI, UploadFile, File, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY_SECRET = os.getenv("MY_PARSER_SECRET", "fallback-secret-key")

def clean_name(text: str) -> str:
    """Оставляет только название до первой запятой."""
    if "Получатель:" in text:
        card = re.search(r"(\d{4,6}\*+\d{4})|(\*+\d{4})", text)
        return f"Перевод {card.group() if card else ''}".strip()
    
    name = re.sub(r"^(Покупка|Оплата|Платеж|Перевод)\s*", "", text, flags=re.IGNORECASE).strip()
    name = name.split(',')[0].strip()
    return name

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
            # 1. Распознаем начало транзакции (Дата + Сумма KZT)
            date_m = re.search(r"(\d{2}\.\d{2}\.\d{4})", line)
            amount_m = re.search(r"(-?\d[\d\s]*\.\d{2})\s*KZT", line)

            if date_m and amount_m:
                if "Пополнение" in line:
                    continue

                is_transfer = "Перевод" in line
                current_amount = float(amount_m.group(1).replace(" ", ""))
                
                # Собираем детализацию ТОЛЬКО до тех пор, пока не встретим новую дату
                details_block = []
                # Начинаем собирать со следующей строки после даты
                for j in range(i + 1, min(i + 5, len(lines))):
                    if re.search(r"\d{2}\.\d{2}\.\d{4}", lines[j]):
                        break # Стоп, это уже другая транзакция
                    details_block.append(lines[j])
                
                full_details = " ".join(details_block)
                
                # --- ПОИСК MCC ---
                mcc = None
                if not is_transfer:
                    # Ищем MCC в текущей строке или в собранном блоке деталей
                    search_zone = line + " " + full_details
                    collapsed = re.sub(r"\s+", "", search_zone)
                    mcc_match = re.search(r"MCC:?(\d{4})", collapsed, re.IGNORECASE)
                    if mcc_match:
                        mcc = mcc_match.group(1)

                # --- ОПРЕДЕЛЕНИЕ ИМЕНИ ---
                # Если в строке с датой есть детализация (запятая), берем оттуда
                if "," in line or "Получатель" in line:
                    name = clean_name(line)
                elif details_block:
                    name = clean_name(details_block[0])
                else:
                    name = "Транзакция"

                transactions.append({
                    "date": date_m.group(1),
                    "amount": current_amount,
                    "name": name,
                    "mcc": mcc,
                    "description": "Перевод" if is_transfer else "Покупка"
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
        return {"transactions": parse_statement_pdf(temp_path)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


