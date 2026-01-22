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

# ТВОИ КОНСТАНТЫ ИЗ ОРИГИНАЛА
IGNORE_PATTERNS = ["С Kaspi Депозита", "На Kaspi Депозит", "На Kaspi депозит", "На свой Счет в Kaspi Pay", "Со своего Счета в Kaspi Pay", "Перевод самому себе", "Пополнение"]
LINE_RE = re.compile(r"^(?P<date>\d{2}\.\d{2}\.\d{2})\s+(?P<amount>[+-]?\s*\d[\d\s,]*)\s*₸\s+(?P<operation>[А-Яа-яA-Za-zЁё]+)\s+(?P<detail>.+)$")

def clean_amount(a: str) -> float:
    return float(a.replace(" ", "").replace(",", "."))

# НОВАЯ ФУНКЦИЯ: ИИ-ОБЕРТКА ДЛЯ ОСТАЛЬНЫХ БАНКОВ
async def parse_with_llm(text: str) -> List[Dict]:
    prompt = f"""Ты экстрактор трат. Извлеки только РАСХОДЫ из этой выписки (Halyk, Freedom, BCC, Alatau, Forte).
    Верни строго JSON: {{"transactions": [{{"date": "DD.MM.YY", "name": "Контрагент", "amount": 100}}]}}
    Очищай 'name' от мусора (MCC, референс, коды).
    Текст: {text[:8000]}""" # Берем первые 8к символов для экономии
    
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
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
            
            # 1. Сначала пробуем твой код для Kaspi
            for line in raw.split("\n"):
                m = LINE_RE.match(line.strip())
                if m:
                    detail = m.group("detail")
                    if any(bad.lower() in detail.lower() for bad in IGNORE_PATTERNS): continue
                    amount = clean_amount(m.group("amount"))
                    if amount < 0:
                        txs.append({"date": m.group("date"), "name": detail.replace("Kaspi Gold", "").strip(), "amount": abs(amount)})
            page.close()

        # 2. Если Kaspi не сработал (txs пусто) — вызываем ИИ для других банков
        if not txs:
            txs = await parse_with_llm(full_text)

        # 3. ТВОЯ УНИВЕРСАЛЬНАЯ ГРУППИРОВКА (теперь работает для всех!)
        merchants = {}
        for tx in txs:
            name = tx["name"]
            if name not in merchants:
                merchants[name] = {"name": name, "total": 0, "count": 0, "transactions": []}
            merchants[name]["total"] += tx["amount"]
            merchants[name]["count"] += 1
            merchants[name]["transactions"].append({"date": tx["date"], "amount": tx["amount"]})
        
        sorted_merchants = sorted(list(merchants.values()), key=lambda x: x["total"], reverse=True)
        daily_stats = defaultdict(float)
        for tx in txs: daily_stats[tx["date"]] += tx["amount"]
        sorted_daily = [{"date": d, "amount": round(a, 2)} for d, a in sorted(daily_stats.items())]

        return {"merchants": sorted_merchants, "daily": sorted_daily}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)




