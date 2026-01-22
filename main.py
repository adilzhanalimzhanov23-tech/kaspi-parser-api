import re
import os
import shutil
import json
import openai
from typing import List, Dict, Optional
from fastapi import FastAPI, UploadFile, File, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from collections import defaultdict
import pypdfium2 as pdf

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY_SECRET = os.getenv("MY_PARSER_SECRET", "fallback-secret-key")
client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ТВОИ КОНСТАНТЫ ДЛЯ KASPI
IGNORE_PATTERNS = ["С Kaspi Депозита", "На Kaspi Депозит", "На Kaspi депозит", "На свой Счет в Kaspi Pay", "Со своего Счета в Kaspi Pay", "Перевод самому себе", "Пополнение"]
LINE_RE = re.compile(r"^(?P<date>\d{2}\.\d{2}\.\d{2})\s+(?P<amount>[+-]?\s*\d[\d\s,]*)\s*₸\s+(?P<operation>[А-Яа-яA-Za-zЁё]+)\s+(?P<detail>.+)$")

def clean_amount(a: str) -> float:
    return float(a.replace(" ", "").replace(",", "."))

# НОВАЯ УЛУЧШЕННАЯ ФУНКЦИЯ ДЛЯ ДРУГИХ БАНКОВ (Forte, Freedom, Halyk и т.д.)
async def parse_with_llm(text: str) -> List[Dict]:
    # Мы просим ИИ не считать суммы, а просто доставать данные и MCC
    prompt = f"""Ты — робот-экстрактор данных. Извлеки ВСЕ траты из текста выписки.
    Для каждой транзакции ОБЯЗАТЕЛЬНО найди MCC код (4 цифры), если он указан в детализации (особенно важно для Forte/Freedom).
    
    Верни строго JSON: 
    {{
      "transactions": [
        {{"date": "DD.MM.YY", "name": "Название магазина", "amount": 100.0, "mcc": "5411"}}
      ]
    }}
    
    ПРАВИЛА:
    1. Не суммируй транзакции. 
    2. Извлекай каждую операцию отдельно.
    3. Если MCC нет, поле mcc оставь null.
    
    Текст: {text[:10000]}"""
    
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": "Ты извлекаешь данные с 100% точностью. Не считаешь суммы сам, только список."},
                  {"role": "user", "content": prompt}],
        response_format={ "type": "json_object" }
    )
    return json.loads(response.choices[0].message.content).get("transactions", [])

@app.post("/analyze")
async def analyze_pdf(file: UploadFile = File(...), x_api_key: Optional[str] = Header(None)):
    if x_api_key != API_KEY_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    temp_path = f"temp_{file.filename}"
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    try:
        doc = pdf.PdfDocument(temp_path)
        txs = []
        full_text = ""

        for i in range(len(doc)):
            page = doc.get_page(i)
            raw = page.get_textpage().get_text_range()
            full_text += raw
            
            # 1. ТВОЙ ОРИГИНАЛЬНЫЙ КОД ДЛЯ KASPI (Regex)
            for line in raw.split("\n"):
                m = LINE_RE.match(line.strip())
                if m:
                    detail = m.group("detail")
                    if any(bad.lower() in detail.lower() for bad in IGNORE_PATTERNS): continue
                    amount = clean_amount(m.group("amount"))
                    if amount < 0:
                        # Сохраняем структуру, добавляем поле mcc (в Kaspi его обычно нет в тексте)
                        txs.append({
                            "date": m.group("date"), 
                            "name": detail.replace("Kaspi Gold", "").strip(), 
                            "amount": abs(amount),
                            "mcc": None
                        })
            page.close()

        # 2. ЕСЛИ НЕ KASPI (или ничего не нашли) — ВКЛЮЧАЕМ ИИ ДЛЯ FORTE/ДРУГИХ
        if not txs:
            txs = await parse_with_llm(full_text)

        # 3. ФИНАЛЬНЫЙ ВОЗВРАТ ДАННЫХ
        # Теперь мы не считаем 'total' здесь, чтобы Lovable сделал это математически точно на фронте.
        # Мы просто возвращаем чистый список транзакций с MCC-кодами.
        return {"transactions": txs}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)





