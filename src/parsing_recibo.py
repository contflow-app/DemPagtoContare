from __future__ import annotations

import os
import json
import re

def _extract_matricula(text: str) -> str | None:
    if not text:
        return None
    m = re.search(r"\bMATR[IÍ]CULA\b\s*[:\-]?\s*(\d{2,10})", text, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"\bMAT\b\s*[:\-]?\s*(\d{2,10})", text, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    return None

from typing import List, Dict, Optional, Tuple

import pdfplumber

CPF_RE = re.compile(r"\b(\d{3}\.\d{3}\.\d{3}-\d{2})\b")
COMP_RE = re.compile(r"\b(\d{2}/\d{4})\b")
MONEY_RE = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})")

def _to_float_br(s: str) -> Optional[float]:
    if not s:
        return None
    t = re.sub(r"[^0-9\.,\-]", "", str(s))
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except Exception:
        return None

def _regex_guess(text: str) -> Dict:
    cpf = None
    mcpf = CPF_RE.search(text or "")
    if mcpf:
        cpf = mcpf.group(1)

    comp = None
    mcomp = re.search(r"\bComp(?:et[êe]ncia)?\s*[:\-]?\s*(\d{2}/\d{4})\b", text or "", re.IGNORECASE)
    if mcomp:
        comp = mcomp.group(1)

    nome = None
    mnome = re.search(r"\bNome\s*[:\-]\s*(.+)", text or "", re.IGNORECASE)
    if mnome:
        nome = mnome.group(1).strip().split("  ")[0].strip().title()

    liquido = None
    mliq = re.search(r"Valor\s+L[ií]quido\s*[:\-]?\s*(" + MONEY_RE.pattern + r")", text or "", re.IGNORECASE)
    if mliq:
        liquido = _to_float_br(mliq.group(1))

    return {"competencia": comp, "nome": nome, "cpf": cpf, "liquido": liquido, "eventos": []}

def _gpt_extract(text: str, model: str) -> Dict:
    try:
        from openai import OpenAI
    except Exception:
        return {}
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {}

    client = OpenAI(api_key=api_key)

    system = (
        "Você é um extrator de holerites brasileiros. "
        "Responda APENAS com JSON. "
        "Não invente; se não tiver certeza, use null."
    )

    user = f"""Extraia do TEXTO do holerite e retorne APENAS JSON no formato:

{{
  "competencia": None,
  "nome": None,
  "cpf": None,
  "liquido": number|null,
  "eventos": [
    {{"codigo": string, "descricao": string, "referencia": None, "provento": number|null, "desconto": number|null}}
  ]
}}

REGRAS IMPORTANTES:
- CPF deve estar no formato 000.000.000-00 (não confundir com CNPJ).
- 'liquido' é o valor líquido do holerite; se estiver 0,00 retorne 0.0.
- 'eventos' deve listar TODAS as verbas do quadro de proventos/descontos.
- Para cada evento, preencha APENAS uma coluna: provento OU desconto (a outra null).
- Converta moeda PT-BR: 1.518,00 -> 1518.00

TEXTO:
{text}
"""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def parse_recibo_pagamento_pdf(pdf_path: str, use_gpt: bool = True, openai_model: str = "gpt-4.1") -> Tuple[List[Dict], Optional[str]]:
    results: List[Dict] = []
    all_text = ""

    with pdfplumber.open(pdf_path) as pdf:
        for idx, page in enumerate(pdf.pages):
            txt = page.extract_text() or ""
            all_text += "\n" + txt

            base = _regex_guess(txt)
            data = _gpt_extract(txt, openai_model) if use_gpt else {}

            # merge (GPT wins when present)
            comp = data.get("competencia") or base.get("competencia")
            nome = data.get("nome") or base.get("nome")
            cpf = data.get("cpf") or base.get("cpf")
            liquido = data.get("liquido")
            if liquido is None:
                liquido = base.get("liquido")

            eventos = data.get("eventos")
            if not isinstance(eventos, list):
                eventos = []
            eventos = _ensure_events_fallback(txt, eventos)

            results.append({
                "page_index": idx,
                "competencia": comp,
                "nome": nome,
                "cpf": cpf,
                "liquido": liquido,
                "eventos": eventos,
                "raw_text": txt,
            })

    # competencia global best-effort
    comp_global = None
    m = re.search(r"\b(\d{2}/\d{4})\b", all_text)
    if m:
        comp_global = m.group(1)

    return results, comp_global

def _find_code_line_value(text: str, code: str) -> tuple[float, float]:
    """Retorna (provento, desconto) para um código, buscando na linha do texto. Best-effort."""
    pro, desc = 0.0, 0.0
    if not text:
        return pro, desc
    for ln in text.splitlines():
        if re.search(rf"\b{re.escape(code)}\b", ln):
            vals = re.findall(r"(\d{1,3}(?:\.\d{3})*,\d{2})", ln)
            if vals:
                v = _to_float_br(vals[-1]) or 0.0
                if re.search(r"\bD\b", ln) or "DESCON" in ln.upper():
                    desc += v
                else:
                    pro += v
    return pro, desc

def _ensure_events_fallback(raw_text: str, eventos: list[dict]) -> list[dict]:
    """Garante verbas críticas (8781/981/998) se existirem no texto e o GPT omitir."""
    present = {str(e.get("codigo","")).strip(): e for e in (eventos or [])}
    out = list(eventos or [])
    for code in ["8781", "981", "998"]:
        if code not in present:
            pro, desc = _find_code_line_value(raw_text or "", code)
            if pro > 0 or desc > 0:
                out.append({
                    "codigo": code,
                    "descricao": "AUTO-FALLBACK",
                    "provento": pro if pro > 0 else None,
                    "desconto": desc if desc > 0 else None,
                })
    return out
