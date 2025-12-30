from __future__ import annotations

import os
import re
import json
from typing import List, Dict, Optional, Tuple

import pdfplumber

CPF_RE = re.compile(r"\b(\d{3}\.\d{3}\.\d{3}-\d{2})\b")
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
    m = MENSALISTA_RE.search(text)
    if not m:
        return None
    mes = m.group(1).upper()
    ano = m.group(2)
    mm = MONTHS_PT.get(mes)
    if not mm:
        return None
    return f"{mm}/{ano}"

def _split_blocks(text: str) -> List[str]:
    # Confirmado: marcador é "Empr."
    starts = [m.start() for m in re.finditer(r"\bEmpr\.\:\s*", text, flags=re.IGNORECASE)]
    if not starts:
        return []
    blocks = []
    for i, s in enumerate(starts):
        e = starts[i+1] if i+1 < len(starts) else len(text)
        blocks.append(text[s:e])
    return blocks

def _extract_cpf(block: str) -> Optional[str]:
    mcpf = CPF_RE.search(block)
    return mcpf.group(1) if mcpf else None

def _extract_nome_cpf(block: str) -> Tuple[Optional[str], Optional[str]]:
    cpf = _extract_cpf(block)
    if not cpf:
        return None, None

    # Melhor estratégia para extrato: pegar a linha onde o CPF aparece e ler o que vem antes
    for ln in block.splitlines():
        if cpf in ln:
            line = re.sub(r"\s+", " ", ln).strip()
            parts = line.split(" ")
            try:
                i = parts.index(cpf)
            except ValueError:
                return None, cpf
            before = parts[:i]
            # remove prefixos numéricos (códigos)
            while before and re.fullmatch(r"\d+", before[0]):
                before = before[1:]
            nome = " ".join(before).strip()
            nome = nome.title() if nome else None
            return nome, cpf

    return None, cpf

def _extract_liquido(block: str) -> Optional[float]:
    # Extrato costuma ter "Valor Líquido"
    m = re.search(r"Valor\s+L[ií]quido\s*[:\-]?\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})", block, re.IGNORECASE)
    if m:
        return parse_brl_money(m.group(1))
    # fallback: procura "Líquido" e pega o primeiro valor monetário após
    m = re.search(r"\bL[ií]quido\b.*?([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})", block, re.IGNORECASE | re.DOTALL)
    if m:
        return parse_brl_money(m.group(1))
    return None

def _extract_cargo_depto(block: str) -> Tuple[Optional[str], Optional[str]]:
    cargo = None
    depto = None
    for ln in block.splitlines():
        u = ln.strip()
        if len(u) >= 8 and u.upper() == u and any(ch.isalpha() for ch in u):
            if any(k in u for k in ["EMPR", "EMPRESA", "CNPJ", "FOLHA", "EXTRATO", "MENSALISTA"]):
                continue
            cargo = u.title()
            break
    m = re.search(r"\bDepto\.?\s*[:\-]?\s*(\d+)", block, re.IGNORECASE)
    if m:
        depto = m.group(1)
    return cargo, depto

def _extract_eventos(block: str) -> List[Dict]:
    eventos = []
    # formato típico do extrato (código, descrição, referência, valor, P/D)
    pat = re.compile(
        r"(?m)^\s*(\d{3,4})\s+(.+?)\s+(\d{1,3}(?:\.\d{3})*,\d{2})\s+(\d{1,3}(?:\.\d{3})*,\d{2})\s*([PD])\s*$"
    )
    for m in pat.finditer(block):
        cod = m.group(1).strip()
        desc = re.sub(r"\s+", " ", m.group(2)).strip()
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

def _find_event_value(eventos: List[Dict], codigo: str, tipo: str) -> Optional[float]:
    for e in eventos:
        if str(e.get("codigo")) == str(codigo):
            return float(e.get("vencimentos" if tipo == "venc" else "descontos") or 0.0)
    return None

def _gpt_extract_name_cpf(block: str, model: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        from openai import OpenAI
    except Exception:
        return None, None
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None, None
    client = OpenAI(api_key=api_key)

    system = "Extraia nome completo e CPF de um bloco de folha. Não invente."
    user = (
        "Retorne SOMENTE JSON {\"nome\": string|null, \"cpf\": string|null}.\n"
        "CPF deve estar no formato 000.000.000-00.\n\n"
        f"TEXTO:\n{block}"
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            response_format={"type":"json_object"},
            temperature=0,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        nome = data.get("nome")
        cpf = data.get("cpf")
        if cpf and not CPF_RE.search(str(cpf)):
            cpf = None
        return (str(nome).strip().title() if nome else None), (str(cpf).strip() if cpf else None)
    except Exception:
        return None, None

def parse_extrato_mensal_pdf(pdf_path: str, use_gpt_fallback: bool=False, openai_model: str="gpt-4.1") -> Tuple[List[Dict], Optional[str]]:
    texts = []
    with pdfplumber.open(pdf_path) as pdf:
        for p in pdf.pages:
            texts.append(p.extract_text() or "")

    full = "\n".join(texts)
    comp = _competencia_global(full)

    blocks = _split_blocks(full)
    colabs: List[Dict] = []

    for b in blocks:
        nome, cpf = _extract_nome_cpf(b)

        if use_gpt_fallback and (nome is None or cpf is None):
            gn, gc = _gpt_extract_name_cpf(b, model=openai_model)
            nome = nome or gn
            cpf = cpf or gc

        liquido = _extract_liquido(b)
        cargo, depto = _extract_cargo_depto(b)
        eventos = _extract_eventos(b)

        v8781 = _find_event_value(eventos, "8781", "venc")
        v981  = _find_event_value(eventos, "981", "desc")

        # fallback: tenta achar 8781/981 direto no texto
        if v8781 is None:
            m = re.search(r"\b8781\b.*?([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})\s*P\b", b.replace("\n", " "), re.IGNORECASE)
            if m:
                v8781 = parse_brl_money(m.group(1))
        if v981 is None:
            m = re.search(r"\b981\b.*?([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})\s*D\b", b.replace("\n", " "), re.IGNORECASE)
            if m:
                v981 = parse_brl_money(m.group(1))

        if cpf or nome or liquido is not None or eventos:
            colabs.append({
                "competencia": comp,
                "nome": nome,
                "cpf": cpf,
                "cargo": cargo,
                "departamento": depto,
                "liquido": liquido,
                "verba_8781": v8781,
                "verba_981": v981,
                "eventos": eventos,
                "raw_block": b,
            })

    return colabs, comp
