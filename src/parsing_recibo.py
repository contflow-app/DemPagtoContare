from __future__ import annotations

import os
import re
import json
from typing import List, Dict, Optional, Tuple
import pdfplumber

CPF_RE = re.compile(r"\b(\d{3}\.\d{3}\.\d{3}-\d{2})\b")
MONEY_RE = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})")
COMP_RE = re.compile(r"\bComp(?:et[êe]ncia)?\s*[:\-]?\s*(\d{2}/\d{4})\b", re.IGNORECASE)
MENSALISTA_RE = re.compile(r"\bMensalista\s+([A-Za-zÇçÃãÕõÁáÉéÍíÓóÚúÂâÊêÔô]+)\s+de\s+(20\d{2})\b", re.IGNORECASE)

MONTHS_PT = {
    "JANEIRO": "01", "FEVEREIRO": "02", "MARÇO": "03", "MARCO": "03",
    "ABRIL": "04", "MAIO": "05", "JUNHO": "06", "JULHO": "07",
    "AGOSTO": "08", "SETEMBRO": "09", "OUTUBRO": "10", "NOVEMBRO": "11", "DEZEMBRO": "12"
}

def parse_brl_money(s: str) -> Optional[float]:
    if not s:
        return None
    t = str(s).strip().replace("\xa0", " ").replace(" ", "")
    t = re.sub(r"[^0-9\.,\-]", "", t)
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except Exception:
        return None

def _competencia_global(text: str) -> Optional[str]:
    m = COMP_RE.search(text)
    if m:
        return m.group(1)
    m = MENSALISTA_RE.search(text)
    if not m:
        return None
    mes = m.group(1).upper()
    ano = m.group(2)
    mm = MONTHS_PT.get(mes)
    if not mm:
        return None
    return f"{mm}/{ano}"

def _extract_name_cpf_regex(text: str) -> Tuple[Optional[str], Optional[str]]:
    cpf = None
    mcpf = CPF_RE.search(text)
    if mcpf:
        cpf = mcpf.group(1)

    m = re.search(r"\bNome\s*[:\-]\s*(.+)", text, re.IGNORECASE)
    if m:
        nome = m.group(1).strip()
        nome = re.split(r"\s{2,}", nome)[0].strip()
        if nome:
            return (nome.title(), cpf)

    if cpf:
        for ln in text.splitlines():
            if cpf in ln:
                ln2 = re.sub(r"\s+", " ", ln).strip()
                parts = ln2.split(" ")
                try:
                    i = parts.index(cpf)
                    before = parts[:i]
                    while before and re.fullmatch(r"\d+", before[0]):
                        before = before[1:]
                    nome = " ".join(before).strip()
                    if nome:
                        return (nome.title(), cpf)
                except ValueError:
                    pass

    return (None, cpf)

def _extract_liquido_regex(text: str) -> Optional[float]:
    m = re.search(r"Valor\s+L[ií]quido\s*[:\-]?\s*(" + MONEY_RE.pattern + r")", text, re.IGNORECASE)
    if m:
        return parse_brl_money(m.group(1))
    m = re.search(r"\bL[ií]quido\b.*?(" + MONEY_RE.pattern + r")", text, re.IGNORECASE | re.DOTALL)
    if m:
        return parse_brl_money(m.group(1))
    return None

def _extract_events(text: str) -> List[Dict]:
    eventos = []
    for ln in text.splitlines():
        ln0 = re.sub(r"\s+", " ", ln).strip()
        m = re.match(r"^(\d{3,4})\s+(.+?)\s+(\d{1,3}(?:\.\d{3})*,\d{2})\s+(\d{1,3}(?:\.\d{3})*,\d{2})\s*([PD])\s*$", ln0)
        if m:
            cod = m.group(1)
            desc = m.group(2).strip()
            ref = m.group(3).strip()
            val = parse_brl_money(m.group(4)) or 0.0
            typ = m.group(5).upper()
            eventos.append({
                "codigo": cod,
                "descricao": desc,
                "referencia": ref,
                "vencimentos": val if typ == "P" else 0.0,
                "descontos": val if typ == "D" else 0.0,
            })
    return eventos

def _find_event_value(text: str, codigo: str) -> Optional[float]:
    for ln in text.splitlines():
        if re.search(r"\b" + re.escape(codigo) + r"\b", ln):
            vals = MONEY_RE.findall(ln)
            if vals:
                return parse_brl_money(vals[-1])
    return None

def _sum_pagamentos_anteriores(eventos: List[Dict]) -> float:
    keywords = ["ADIANT", "ANTEC", "PAGTO", "PAGAMENTO", "ANTERIOR"]
    total = 0.0
    for e in eventos:
        desc = str(e.get("descricao","")) .upper()
        if any(k in desc for k in keywords):
            total += float(e.get("vencimentos") or 0.0)
    return total

def _gpt_extract(page_text: str, model: str) -> Dict:
    try:
        from openai import OpenAI
    except Exception:
        return {}
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {}
    client = OpenAI(api_key=api_key)

    system = (
        "Você é um extrator determinístico de holerites/recibos. " 
        "Extraia SOMENTE informações presentes. Não invente."
    )
    user = (
        "Extraia e retorne SOMENTE JSON com as chaves:\n"
        "{\"nome\": string|null, \"cpf\": string|null, \"liquido\": number|null, \"verba_8781\": number|null, \"verba_981\": number|null}\n\n"
        "Regras:\n- cpf deve estar no formato 000.000.000-00.\n- Valores são números (float) em reais.\n- Se não encontrar, use null.\n\n"
        "TEXTO DA PÁGINA (recibo):\n"
        f"{page_text}"
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            response_format={"type":"json_object"},
            temperature=0,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def parse_recibo_pagamento_pdf(pdf_path: str, use_gpt: bool=True, openai_model: str="gpt-4.1") -> Tuple[List[Dict], Optional[str]]:
    colabs: List[Dict] = []
    all_text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for p in pdf.pages:
            txt = p.extract_text() or ""
            all_text += "\n" + txt

            comp = _competencia_global(txt)
            nome, cpf = _extract_name_cpf_regex(txt)
            liquido = _extract_liquido_regex(txt)

            eventos = _extract_events(txt)
            v8781 = _find_event_value(txt, "8781")
            v981 = _find_event_value(txt, "981")
            pagamentos_anteriores = _sum_pagamentos_anteriores(eventos)

            if use_gpt and (nome is None or cpf is None or liquido is None or v8781 is None or v981 is None):
                g = _gpt_extract(txt, model=openai_model)
                if nome is None and g.get("nome"): nome = str(g.get("nome")).strip().title()
                if cpf is None and g.get("cpf"): cpf = str(g.get("cpf")).strip()
                if liquido is None and g.get("liquido") is not None:
                    try: liquido = float(g.get("liquido"))
                    except Exception: pass
                if v8781 is None and g.get("verba_8781") is not None:
                    try: v8781 = float(g.get("verba_8781"))
                    except Exception: pass
                if v981 is None and g.get("verba_981") is not None:
                    try: v981 = float(g.get("verba_981"))
                    except Exception: pass

            colabs.append({
                "competencia": comp,
                "nome": nome,
                "cpf": cpf,
                "liquido": liquido,
                "verba_8781": v8781,
                "verba_981": v981,
                "pagamentos_anteriores": pagamentos_anteriores,
                "eventos": eventos,
                "raw_text": txt,
            })

    comp_global = _competencia_global(all_text)
    return colabs, comp_global
