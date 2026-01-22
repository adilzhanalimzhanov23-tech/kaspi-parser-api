import re
from decimal import Decimal, InvalidOperation
from typing import List, Dict
import pypdfium2 as pdf


# =========================
# REGEX
# =========================

DATE_RE = re.compile(r"\d{2}\.\d{2}\.\d{4}")
NEG_AMOUNT_RE = re.compile(r"-(\d[\d\s]*\.\d{2})\s*KZT")
MCC_RE = re.compile(r"MCC[:\s]*([\d\s]+)")
POPOLNENIE_RE = re.compile(r"Пополнение", re.IGNORECASE)


# =========================
# HELPERS
# =========================

def clean_amount(val: str) -> Decimal:
    return Decimal(val.replace(" ", ""))


def extract_mcc(text: str) -> str | None:
    """
    Извлекает MCC даже если он разорван переносами
    """
    m = MCC_RE.search(text)
    if not m:
        return None
    return "".join(re.findall(r"\d", m.group(1)))


def extract_detail_name(detail: str) -> str:
    """
    Берём всё до первой запятой
    """
    return detail.split(",")[0].strip()


# =========================
# MAIN PARSER
# =========================

def parse_statement_pdf(path: str) -> List[Dict]:
    doc = pdf.PdfDocument(path)
    lines: List[str] = []

    for page in doc:
        textpage = page.get_textpage()
        text = textpage.get_text_range()
        lines.extend([l.strip() for l in text.splitlines() if l.strip()])

    transactions: List[Dict] = []

    current = None  # текущая собираемая транзакция

    for line in lines:
        # 1️⃣ новая строка с датой + суммой
        date_match = DATE_RE.search(line)
        amount_match = NEG_AMOUNT_RE.search(line)

        if date_match and amount_match:
            # закрываем предыдущую
            if current:
                transactions.append(current)

            # если это пополнение — пропускаем
            if POPOLNENIE_RE.search(line):
                current = None
                continue

            amount = clean_amount(amount_match.group(1))

            current = {
                "date": date_match.group(),
                "amount": amount,
                "description": "Покупка" if "Покупка" in line else "",
                "detail_name": "",
                "mcc": None,
                "_detail_raw": ""
            }
            continue

        # 2️⃣ продолжаем детализацию
        if current:
            current["_detail_raw"] += " " + line

            # если MCC появился — пробуем вытащить
            mcc = extract_mcc(current["_detail_raw"])
            if mcc:
                current["mcc"] = mcc

            # если имя ещё не заполнено
            if not current["detail_name"]:
                current["detail_name"] = extract_detail_name(current["_detail_raw"])

    # закрываем последнюю
    if current:
        transactions.append(current)

    # чистим техническое поле
    for tx in transactions:
        tx.pop("_detail_raw", None)

    return transactions


# =========================
# EXAMPLE
# =========================

if __name__ == "__main__":
    data = parse_statement_pdf("Сформированная выписка (4).pdf")
    for d in data[:5]:
        print(d)











