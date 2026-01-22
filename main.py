import os, re, json, shutil
import pypdfium2 as pdf
from fastapi import FastAPI, UploadFile, File, Header
from openai import OpenAI

app = FastAPI()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# 1. Жесткий экстрактор (Код)
def hard_extract_lines(filepath):
    lines_with_data = []
    doc = pdf.PdfDocument(filepath)
    for page in doc:
        text = page.get_textpage().get_text_range()
        for line in text.splitlines():
            # Если в строке есть дата и символ валюты — это наш кандидат
            if re.search(r"\d{2}\.\d{2}\.\d{2,4}", line) and any(c in line for c in ["₸", "KZT", "T"]):
                lines_with_data.append(line.strip())
    return lines_with_data

# 2. Умный нормализатор (ИИ)
def ai_normalize(raw_lines):
    prompt = f"""Преврати эти строки банковской выписки в JSON. 
    ОЧЕНЬ ВАЖНО: 
    - Разделяй Yandex Go: если такси — 'Yandex Taxi', если еда — 'Yandex Eats'.
    - Сумму бери СТРОГО из текста, только число.
    - MCC вытаскивай (4 цифры).
    Строки:
    {chr(10).join(raw_lines)}
    
    Верни JSON: {{"transactions": [{{"date": "...", "amount": 123.0, "name": "...", "mcc": "..."}}]}}"""
    
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content).get("transactions", [])

@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    path = f"/tmp/{file.filename}"
    with open(path, "wb") as f: f.write(await file.read())
    
    try:
        # Сначала код находит все важные строки
        raw_data = hard_extract_lines(path)
        if not raw_data:
            return {"transactions": []}
            
        # Потом ИИ их структурирует
        final_txs = ai_normalize(raw_data)
        return {"transactions": final_txs}
    finally:
        if os.path.exists(path): os.remove(path)









