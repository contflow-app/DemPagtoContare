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
    if s is None:
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
    m = COMP_RE.search(text or "")
    if m:
        return m.group(1)
    m = MENSALISTA_RE.search(text or "")
    if not m:
        return None
    mes = m.group(1).upper()
    ano = m.group(2)
    mm = MONTHS_PT.get(mes)
    if not mm:
        return None
    return f"{mm}/{ano}"

def _extract_name_cpf_regex(text: str) -> Tuple[Optional[str], Optional[str]]:
    text = text or ""
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
    text = text or ""
    m = re.search(r"Valor\s+L[ií]quido\s*[:\-]?\s*(" + MONEY_RE.pattern + r")", text, re.IGNORECASE)
    if m:
        return parse_brl_money(m.group(1))
    m = re.search(r"\bL[ií]quido\b.*?(" + MONEY_RE.pattern + r")", text, re.IGNORECASE | re.DOTALL)
    if m:
        return parse_brl_money(m.group(1))
    return None

def _extract_events_regex(text: str) -> List[Dict]:
    eventos: List[Dict] = []
    for ln in (text or "").splitlines():
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

def _gpt_extract_holerite(page_text: str, model: str) -> Dict:
    """GPT como recurso principal: extrai nome/cpf/competência/líquido + tabela de eventos (verbas)."""
    try:
        from openai import OpenAI
    except Exception:
        return {}
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {}
    client = OpenAI(api_key=api_key)

    system = (
        "Você é um extrator de HOLERITES brasileiros altamente preciso. "
        "Sua tarefa é transformar texto de holerite em JSON estruturado. "
        "Não invente. Se não tiver evidência clara no texto, use null. "
        "NUNCA retorne texto fora do JSON."
    )

    # Few-shot (exemplo real) para guiar a extração de verbas/eventos.
    # Números no JSON devem usar ponto como separador decimal.
    example_input = (
        "EXEMPLO (trecho simplificado):
"
        "8781 SALARIO CONTRATUAL. 1.518,00 P
"
        "990 ESTOURO DO MES 120,70 P
"
        "686 DESC. CURSO 89,50 D
"
        "998 I.N.S.S 113,85 D
"
        "991 ESTOURO MES ANTERIOR 35,35 D
"
        "981 DESC ADIANTAMENTO SALARIAL 1.400,00 D
"
        "Líquido folha (provento - desconto) 0,00
"
        "Competência 11/2025
"
        "Nome ALANA ...
"
        "CPF 000.000.000-00
"
    )

    example_output = {
        "competencia": "11/2025",
        "nome": "Alana",
        "cpf": "000.000.000-00",
        "liquido": 0.0,
        "eventos": [
            {"codigo": "8781", "descricao": "SALARIO CONTRATUAL.", "referencia": None, "provento": 1518.00, "desconto": None},
            {"codigo": "990", "descricao": "ESTOURO DO MES", "referencia": None, "provento": 120.70, "desconto": None},
            {"codigo": "686", "descricao": "DESC. CURSO", "referencia": None, "provento": None, "desconto": 89.50},
            {"codigo": "998", "descricao": "I.N.S.S", "referencia": None, "provento": None, "desconto": 113.85},
            {"codigo": "991", "descricao": "ESTOURO MES ANTERIOR", "referencia": None, "provento": None, "desconto": 35.35},
            {"codigo": "981", "descricao": "DESC ADIANTAMENTO SALARIAL", "referencia": None, "provento": None, "desconto": 1400.00},
        ],
    }

    user = (
        "Extraia do TEXTO do holerite e retorne APENAS JSON no formato:
"
        "{
"
        '  "competencia": string|null,
'
        '  "nome": string|null,
'
        '  "cpf": string|null,
'
        '  "liquido": number|null,
'
        '  "eventos": [
'
        '     {"codigo": string, "descricao": string, "referencia": string|null, "provento": number|null, "desconto": number|null}
'
        "  ]
"
        "}

"
        "Regras IMPORTANTES:
"
        "1) Use apenas dados com evidência no texto.
"
        "2) CPF no formato 000.000.000-00 (não confundir com CNPJ).
"
        "3) 'liquido' é o valor líquido do holerite. Se for '0,00', retorne 0.0.
"
        "4) 'eventos': extraia TODAS as verbas/linhas de provento/desconto. Ignore linhas de totais.
"
        "5) Para cada evento, preencha APENAS uma coluna: 'provento' OU 'desconto' (a outra deve ser null).
"
        "6) Converta moeda PT-BR para número: '1.518,00' -> 1518.00.
"
        "7) Se houver códigos como 8781, 981, 998, preserve exatamente (string).

"
        "EXEMPLO de como você deve responder:
"
        f"Entrada:
{example_input}

"
        f"Saída (JSON):
{json.dumps(example_output, ensure_ascii=False)}

"
        "AGORA extraia do TEXTO real abaixo:
"
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

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {}
    client = OpenAI(api_key=api_key)

    system = (
        "Você é um extrator de holerites brasileiros. "
        "Retorne APENAS o JSON solicitado. Não invente dados."
    )

    user = (
        "Extraia do texto de um holerite/recibo e retorne APENAS JSON no formato:\n"
        "{\n"
        "  \"competencia\": string|null,\n"
        "  \"nome\": string|null,\n"
        "  \"cpf\": string|null,\n"
        "  \"liquido\": number|null,\n"
        "  \"eventos\": [\n"
        "     {\"codigo\": string, \"descricao\": string, \"referencia\": string|null, \"provento\": number|null, \"desconto\": number|null}\n"
        "  ]\n"
        "}\n\n"
        "Regras:\n"
        "- CPF deve estar no formato 000.000.000-00.\n"
        "- 'liquido' é o valor líquido do holerite; se estiver zerado, retorne 0.\n"
        "- 'eventos' deve listar TODAS as verbas do quadro de proventos/descontos.\n"
        "- Para cada evento, preencha 'provento' OU 'desconto' conforme a coluna; a outra deve ser null ou 0.\n"
        "- Não inclua CNPJ da empresa como CPF.\n"
        "- Se algo não existir com certeza no texto, use null.\n\n"
        "TEXTO:\n"
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
        for idx, p in enumerate(pdf.pages):
            txt = p.extract_text() or ""
            all_text += "\n" + txt

            comp = _competencia_global(txt)
            nome, cpf = _extract_name_cpf_regex(txt)
            liquido = _extract_liquido_regex(txt)
            eventos = _extract_events_regex(txt)

            if use_gpt:
                g = _gpt_extract_holerite(txt, model=openai_model)
                if g:
                    comp = comp or g.get("competencia")
                    if nome is None and g.get("nome"):
                        nome = str(g.get("nome")).strip().title()
                    if cpf is None and g.get("cpf"):
                        cpf = str(g.get("cpf")).strip()
                    if liquido is None and g.get("liquido") is not None:
                        try: liquido = float(g.get("liquido"))
                        except Exception: pass
                    # eventos GPT (prioritário se vier algo)
                    ge = g.get("eventos")
                    if isinstance(ge, list) and len(ge) > 0:
                        eventos = []
                        for e in ge:
                            if not isinstance(e, dict):
                                continue
                            eventos.append({
                                "codigo": str(e.get("codigo") or "").strip(),
                                "descricao": str(e.get("descricao") or "").strip(),
                                "referencia": (str(e.get("referencia")).strip() if e.get("referencia") not in [None, "None", "null"] else None),
                                "vencimentos": float(e.get("provento") or 0.0) if e.get("provento") is not None else 0.0,
                                "descontos": float(e.get("desconto") or 0.0) if e.get("desconto") is not None else 0.0,
                            })

            colabs.append({
                "page_index": idx,
                "competencia": comp,
                "nome": nome,
                "cpf": cpf,
                "liquido": liquido,
                "eventos": eventos,
                "raw_text": txt,
            })

    comp_global = _competencia_global(all_text)
    return colabs, comp_global
