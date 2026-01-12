import re
import os
import shutil
from typing import List, Dict
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pypdfium2 as pdf

app = FastAPI()

# Разрешаем Lovable подключаться к API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

IGNORE_PATTERNS = [
    "С Kaspi Депозита", "На Kaspi Депозит", "На Kaspi депозит",
    "На свой Счет в Kaspi Pay", "Со своего Счета в Kaspi Pay",
    "Перевод самому себе", "Пополнение" 
]

LINE_RE = re.compile(
    r"^(?P<date>\d{2}\.\d{2}\.\d{2})\s+"
    r"(?P<amount>[+-]?\s*\d[\d\s,]*)\s*₸\s+"
    r"(?P<operation>[А-Яа-яA-Za-zЁё]+)\s+"
    r"(?P<detail>.+)$"
)

def clean_amount(a: str) -> float:
    a = a.replace(" ", "").replace(",", ".")
    return float(a)

@app.post("/analyze")
async def analyze_pdf(file: UploadFile = File(...)):
    temp_path = f"temp_{file.filename}"
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    try:
        doc = pdf.PdfDocument(temp_path)
        txs = []
        for i in range(len(doc)):
            page = doc.get_page(i)
            textpage = page.get_textpage()
            raw = textpage.get_text_range()
            for line in raw.split("\n"):
                line = line.strip()
                m = LINE_RE.match(line)
                if m:
                    detail = m.group("detail")
                    if any(bad.lower() in detail.lower() for bad in IGNORE_PATTERNS):
                        continue
                    
                    amount = clean_amount(m.group("amount"))
                    if amount < 0: # Нам нужны только расходы
                        txs.append({
                            "name": detail.replace("Kaspi Gold", "").strip(),
                            "amount": abs(amount)
                        })
            page.close()

        # Группировка по контрагентам
        merchants = {}
        for tx in txs:
            name = tx["name"]
            if name not in merchants:
                merchants[name] = {"name": name, "total": 0, "count": 0}
            merchants[name]["total"] += tx["amount"]
            merchants[name]["count"] += 1
            
        return sorted(list(merchants.values()), key=lambda x: x["total"], reverse=True)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

