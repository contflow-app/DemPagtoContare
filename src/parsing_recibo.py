from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

import pdfplumber

# Regex de dinheiro pt-BR (1.234,56)
MONEY_RE = re.compile(r"(?:\d{1,3}(?:\.\d{3})*|\d+),(?:\d{2})")
CPF_RE = re.compile(r"\b(\d{3}\.\d{3}\.\d{3}-\d{2})\b")

_MES_MAP = {
    "JANEIRO": "01",
    "FEVEREIRO": "02",
    "MARCO": "03",
    "MARÇO": "03",
    "ABRIL": "04",
    "MAIO": "05",
    "JUNHO": "06",
    "JULHO": "07",
    "AGOSTO": "08",
    "SETEMBRO": "09",
    "OUTUBRO": "10",
    "NOVEMBRO": "11",
    "DEZEMBRO": "12",
}


def parse_money_any(x) -> Optional[float]:
    """Converte moeda pt-BR para float. Aceita float/int/string."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if not s:
        return None
    s = re.sub(r"[^0-9,\.\-]", "", s)
    if not s:
        return None
    # se tem vírgula, assume decimal pt-BR
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def _extract_cpf(text: str) -> Optional[str]:
    m = CPF_RE.search(text or "")
    return m.group(1) if m else None


def _regex_guess(text: str) -> Dict:
    """Extração determinística para holerites Contare (CNPJ + Mensalista + tabela de verbas)."""
    text = text or ""

    cpf = _extract_cpf(text)

    # Competência por 'Mensalista <MÊS> de <AAAA>' (evita capturar 'Admissão dd/mm/aaaa')
    comp = None
    m1 = re.search(r"Mensalista\s+([A-Za-zÇçÁÉÍÓÚÂÊÔÃÕÀ-Üà-ü]+)\s+de\s+(\d{4})", text, flags=re.IGNORECASE)
    if m1:
        mes = m1.group(1).strip().upper()
        mes = (mes.replace("Ç","C").replace("Ã","A").replace("Á","A").replace("Â","A").replace("À","A")
                  .replace("É","E").replace("Ê","E").replace("Í","I").replace("Ó","O").replace("Ô","O")
                  .replace("Õ","O").replace("Ú","U"))
        ano = m1.group(2)
        mm = _MES_MAP.get(mes)
        if mm:
            comp = f"{mm}/{ano}"

    # Nome pela linha após "Código Nome do Funcionário"
    nome = None
    m2 = re.search(
        r"C[oó]digo\s+Nome\s+do\s+Funcion[aá]rio[\s\S]{0,250}?(?:\n|\r\n)\s*\d+\s+([A-ZÇÁÉÍÓÚÂÊÔÃÕÀ-Ü ]{5,80}?)\s+\d{6}\b",
        text,
        flags=re.IGNORECASE,
    )
    if m2:
        nome = m2.group(1).strip().title()

    # Líquido
    liquido = None
    mliq = re.search(rf"Valor\s+L[ií]quido\s*(?:[:\-])?\s*({MONEY_RE.pattern})", text, flags=re.IGNORECASE)
    if mliq:
        liquido = parse_money_any(mliq.group(1))

    # Eventos
    eventos: List[Dict] = []
    in_table = False
    done_first_copy = False
    for ln in text.splitlines():
        s = " ".join(ln.split())
        if not s:
            continue
        if re.search(r"C[oó]digo\s+Descri[cç][aã]o\s+Refer[eê]ncia\s+Vencimentos\s+Descontos", s, flags=re.IGNORECASE):
            if done_first_copy:
                break
            in_table = True
            continue
        if in_table and re.search(r"Total\s+de\s+Vencimentos|Total\s+de\s+Descontos|Valor\s+L[ií]quido", s, flags=re.IGNORECASE):
            in_table = False
            if eventos:
                done_first_copy = True
            continue
        if in_table:
            # padrão mais comum: COD DESC REF VENC [DESC]
            m = re.match(
                r"^(\d{3,4})\s+(.+?)\s+(\S+)\s+(\d{1,3}(?:\.\d{3})*,\d{2})(?:\s+(\d{1,3}(?:\.\d{3})*,\d{2}))?$",
                s,
            )
            if m:
                codigo = m.group(1)
                desc = m.group(2).strip()
                ref = m.group(3)
                v1 = parse_money_any(m.group(4))
                v2 = parse_money_any(m.group(5)) if m.group(5) else None

                provento = v1
                desconto = None
                if v2 is not None:
                    # quando há 2 valores monetários, tratamos como vencimentos e descontos
                    provento = v1 if (v1 or 0) != 0 else None
                    desconto = v2 if (v2 or 0) != 0 else None
                else:
                    # quando há apenas 1 valor após a referência, pode ser DESCONTO (vencimentos em branco)
                    desc_up = desc.upper()
                    if desc_up.startswith('DESC') or 'INSS' in desc_up or 'I.N.S.S' in desc_up or 'RESSARC' in desc_up or 'PREJUI' in desc_up or 'ATRAS' in desc_up or 'FALTA' in desc_up or 'MULTA' in desc_up or 'PENAL' in desc_up or codigo in {'981','998','686','255','8069'}:
                        provento = None
                        desconto = v1 if (v1 or 0) != 0 else None

                eventos.append({
                    "codigo": codigo,
                    "descricao": desc,
                    "referencia": ref,
                    "provento": provento,
                    "desconto": desconto,
                })

    # dedup simples
    seen = set()
    dedup = []
    for e in eventos:
        key = (e.get('codigo'), e.get('descricao'), e.get('referencia'), e.get('provento'), e.get('desconto'))
        if key in seen:
            continue
        seen.add(key)
        dedup.append(e)

    return {"competencia": comp, "nome": nome, "cpf": cpf, "liquido": liquido, "eventos": dedup}


def _gpt_extract(text: str, model: str) -> Dict:
    """GPT para preencher campos faltantes / eventos. Só roda se OPENAI_API_KEY estiver definido."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {}

    try:
        from openai import OpenAI
        import json
    except Exception:
        return {}

    client = OpenAI(api_key=api_key)

    system = (
        "Você extrai dados de holerites brasileiros. Responda APENAS JSON. "
        "Não invente; se não encontrar no texto, use null."
    )
    payload = {
        "texto": text[:12000],  # limite prático
        "saida": {
            "competencia": "MM/AAAA ou null",
            "nome": "string ou null",
            "cpf": "000.000.000-00 ou null",
            "liquido": "number ou null",
            "eventos": [
                {"codigo": "string", "descricao": "string", "referencia": "string|null", "provento": "number|null", "desconto": "number|null"}
            ]
        }
    }

    try:
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.output_text or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def parse_recibo_pagamento_pdf(pdf_path: str, use_gpt: bool = True, openai_model: str = "gpt-4.1") -> Tuple[List[Dict], Optional[str]]:
    results: List[Dict] = []
    competencia_global: Optional[str] = None

    with pdfplumber.open(pdf_path) as pdf:
        for idx, page in enumerate(pdf.pages):
            txt = page.extract_text() or ""

            base = _regex_guess(txt)
            data = _gpt_extract(txt, openai_model) if use_gpt else {}

            # merge: GPT só sobrescreve quando traz valor útil
            comp = data.get("competencia") or base.get("competencia")
            nome = data.get("nome") or base.get("nome")
            cpf = data.get("cpf") or base.get("cpf")
            liquido = data.get("liquido")
            if liquido is None:
                liquido = base.get("liquido")

            eventos = data.get("eventos")
            if not isinstance(eventos, list) or len(eventos) == 0:
                eventos = base.get("eventos") or []

            if comp and not competencia_global:
                competencia_global = comp

            results.append({
                "page_index": idx,
                "competencia": comp,
                "nome": nome,
                "cpf": cpf,
                "liquido": liquido,
                "eventos": eventos,
                "raw_text": txt,
            })

    return results, competencia_global
