import re


def normalize_phone(raw: str) -> str | None:
    """Normaliza para 55 + DDD(2) + 9XXXXXXXX(9) = 13 digitos. Apenas celulares brasileiros."""
    number = re.sub(r"\D", "", str(raw))
    # Adiciona DDI 55 se vier só DDD + celular (11 digitos: DDD + 9 + 8 digitos)
    if len(number) == 11:
        number = "55" + number
    # Aceita apenas 55 + DDD(2) + celular(9) = 13 digitos (qualquer DDD)
    if len(number) != 13 or not number.startswith("55"):
        return None
    return number


def normalize_name(raw: str) -> str:
    name = str(raw).strip()
    name = re.sub(r"\s+", " ", name)
    return name.title() if name else "Contato"


def clean_contacts(raw_list: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Recebe lista de dicts com chaves 'name' e 'number'.
    Retorna (válidos, excluídos) — sem duplicatas, sem inválidos.
    """
    seen = set()
    cleaned = []
    rejected = []
    for item in raw_list:
        number = normalize_phone(item.get("number", ""))
        if not number:
            rejected.append({"name": item.get("name", ""), "number": item.get("number", ""), "reason": "número inválido"})
            continue
        if number in seen:
            rejected.append({"name": item.get("name", ""), "number": item.get("number", ""), "reason": "duplicado"})
            continue
        seen.add(number)
        cleaned.append({
            "name": normalize_name(item.get("name", "")),
            "number": number,
        })
    return cleaned, rejected
