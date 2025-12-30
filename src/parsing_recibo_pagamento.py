from __future__ import annotations

import os
import re
import json
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

import pdfplumber


MONTHS_PT = {
    "JANEIRO": "01", "FEVEREIRO": "02", "MARÇO": "03", "MARCO": "03",
    "ABRIL": "04", "MAIO": "05", "JUNHO": "06", "JULHO": "07",
    "AGOSTO": "08", "SETEMBRO": "09", "OUTUBRO": "10", "NOVEMBRO": "11", "DEZEMBRO": "12"
}

CPF_RE = re.compile(r"\b(\d{3}\.\d{3}\.\d{3}-\d{2})\b")

def cpf_digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")

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

def competencia_from_header(text: str) -> Optional[str]:
    m = re.search(r"\bMensalista\s+([A-Za-zÇçÃãÕõÁáÉéÍíÓóÚúÂâÊêÔô]+)\s+de\s+(20\d{2})\b", text, re.IGNORECASE)
    if not m:
        return None
    mes = m.group(1).upper()
    ano = m.group(2)
    mm = MONTHS_PT.get(mes)
    if not mm:
        return None
    return f"{mm}/{ano}"

@dataclass
class EventoFolha:
    codigo: str
    descricao: str
    referencia: Optional[str] = None
    vencimentos: float = 0.0
    descontos: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "codigo": self.codigo,
            "descricao": self.descricao,
            "referencia": self.referencia,
            "vencimentos": float(self.vencimentos or 0.0),
            "descontos": float(self.descontos or 0.0),
        }

@dataclass
class ReciboFolha:
    page_index: int = -1
    competencia: Optional[str] = None
    nome: Optional[str] = None
    cpf: Optional[str] = None
    cbo: Optional[str] = None
    departamento: Optional[str] = None
    cargo: Optional[str] = None
    matricula: Optional[str] = None

    total_venc: Optional[float] = None
    total_desc: Optional[float] = None
    liquido: Optional[float] = None

    eventos: List[EventoFolha] = field(default_factory=list)
    raw_text: str = ""

    def evento_valor(self, codigo: str, tipo: str = "venc") -> Optional[float]:
        for e in self.eventos:
            if e.codigo == str(codigo):
                return float(e.vencimentos) if tipo == "venc" else float(e.descontos)
        return None


def _extract_totais(text: str) -> Dict[str, Optional[float]]:
    tv = td = liq = None
    m = re.search(r"Total\s+de\s+Vencimentos.*?([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})", text, re.IGNORECASE | re.DOTALL)
    if m:
        tv = parse_brl_money(m.group(1))
    m = re.search(r"Total\s+de\s+Descontos.*?([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})", text, re.IGNORECASE | re.DOTALL)
    if m:
        td = parse_brl_money(m.group(1))
    m = re.search(r"Valor\s+L[ií]quido.*?([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})", text, re.IGNORECASE | re.DOTALL)
    if m:
        liq = parse_brl_money(m.group(1))
    return {"total_venc": tv, "total_desc": td, "liquido": liq}


def _extract_nome_cpf_line_flexible(text: str, r: ReciboFolha) -> None:
    """
    Extração robusta:
    1) acha o CPF em qualquer lugar
    2) pega a linha que contém o CPF e parseia tokens antes dele como nome
    3) tenta pegar CBO/Departamento/Filial após o CPF (se houver)
    """
    mcpf = CPF_RE.search(text)
    if not mcpf:
        return
    cpf = mcpf.group(1)
    r.cpf = cpf

    # pega a linha que contém o cpf
    line = None
    for ln in text.splitlines():
        if cpf in ln:
            line = ln
            break
    if not line:
        return

    # Ex típico: "19 ALANA DE OLIVEIRA ROSA 087.724.856-71 411030 1 1"
    # Remove múltiplos espaços
    line_clean = re.sub(r"\s+", " ", line).strip()
    parts = line_clean.split(" ")

    # encontra índice do cpf
    try:
        i = parts.index(cpf)
    except ValueError:
        # às vezes vem com caracteres estranhos, tenta match por regex no join
        joined = " ".join(parts)
        m = CPF_RE.search(joined)
        if not m:
            return
        cpf = m.group(1)
        r.cpf = cpf
        i = joined.split(" ").index(cpf)

    # antes do CPF: [codigo] + nome...
    before = parts[:i]
    # remove primeiro token se for código
    if before and re.fullmatch(r"\d+", before[0]):
        before = before[1:]
    if before:
        r.nome = " ".join(before).title()

    # depois do CPF: CBO, depto, filial (se existirem)
    after = parts[i+1:]
    if len(after) >= 1 and re.fullmatch(r"\d{4,6}", after[0]):
        r.cbo = after[0]
    if len(after) >= 2 and re.fullmatch(r"\d+", after[1]):
        r.departamento = after[1]


def _extract_cargo(text: str, r: ReciboFolha) -> None:
    # Cargo: linha em caps antes de "PIS"
    m = re.search(r"(?m)^\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ\s]{6,})\s+PIS\b", text)
    if m:
        r.cargo = re.sub(r"\s+", " ", m.group(1)).strip().title()


def _extract_matricula(text: str, r: ReciboFolha) -> None:
    # Captura um número longo isolado (PIS costuma vir na linha seguinte)
    m = re.search(r"(?m)^\s*(\d{9,14})\s*$", text)
    if m:
        r.matricula = m.group(1)


def _extract_eventos_from_table(page) -> List[EventoFolha]:
    eventos: List[EventoFolha] = []
    try:
        tables = page.extract_tables({
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "intersection_tolerance": 5,
            "snap_tolerance": 3,
            "join_tolerance": 3,
            "edge_min_length": 3,
        }) or []
    except Exception:
        tables = []

    for t in tables:
        if not t or len(t) < 2:
            continue
        header = " ".join([str(x or "") for x in t[0]]).upper()
        if ("CÓD" in header or "COD" in header) and ("VENC" in header or "VENCIMENT" in header):
            for row in t[1:]:
                cells = [str(c or "").strip() for c in row]
                if not cells:
                    continue
                codigo = re.sub(r"\D", "", cells[0]) if cells[0] else ""
                if not codigo:
                    continue
                descricao = re.sub(r"\s+", " ", cells[1]) if len(cells) > 1 else ""
                referencia = cells[2] if len(cells) > 2 else None
                venc = parse_brl_money(cells[3]) if len(cells) > 3 else 0.0
                desc = parse_brl_money(cells[4]) if len(cells) > 4 else 0.0
                eventos.append(EventoFolha(codigo=codigo, descricao=descricao, referencia=referencia, vencimentos=venc or 0.0, descontos=desc or 0.0))
            if eventos:
                return eventos
    return eventos


def _extract_eventos_regex(text: str) -> List[EventoFolha]:
    eventos: List[EventoFolha] = []
    # Linhas típicas: "8781 SALARIO CONTRATUAL. 30,00 1.518,00" (às vezes sem P/D visível)
    # Vamos pegar: codigo + descrição + referência + valor1 + (valor2 opcional)
    pat = re.compile(
        r"(?m)^\s*(\d{3,4})\s+(.+?)\s+(\d{1,3}(?:\.\d{3})*,\d{2})\s+(\d{1,3}(?:\.\d{3})*,\d{2})(?:\s+(\d{1,3}(?:\.\d{3})*,\d{2}))?\s*$"
    )
    for m in pat.finditer(text):
        cod = m.group(1).strip()
        desc = re.sub(r"\s+", " ", m.group(2)).strip()
        ref = m.group(3).strip()
        v1 = parse_brl_money(m.group(4)) or 0.0
        v2 = parse_brl_money(m.group(5)) if m.group(5) else None

        # heurística: se descrição indica desconto, joga em descontos
        if "DESC" in desc.upper() or "DESCONTO" in desc.upper():
            descontos = v2 if v2 is not None else v1
            venc = 0.0
        else:
            venc = v1
            descontos = v2 if v2 is not None else 0.0

        eventos.append(EventoFolha(codigo=cod, descricao=desc, referencia=ref, vencimentos=venc, descontos=descontos))
    return eventos


def _gpt_extract_eventos(text: str, model: str) -> List[EventoFolha]:
    try:
        from openai import OpenAI
    except Exception:
        return []
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return []
    client = OpenAI(api_key=api_key)

    system = "Extraia a lista de eventos (verbas) de um recibo de pagamento. Não invente."
    user = (
        "Do texto a seguir, extraia uma lista JSON de eventos com chaves:\n"
        "codigo (string), descricao (string), referencia (string|null), vencimentos (number), descontos (number).\n"
        "Retorne SOMENTE JSON no formato {\"eventos\":[...]}.\n\n"
        f"TEXTO:\n{text}"
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            response_format={"type":"json_object"},
            temperature=0,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        out: List[EventoFolha] = []
        for e in data.get("eventos", [])[:250]:
            out.append(EventoFolha(
                codigo=str(e.get("codigo","")).strip(),
                descricao=str(e.get("descricao","")).strip(),
                referencia=(str(e.get("referencia")).strip() if e.get("referencia") is not None else None),
                vencimentos=float(e.get("vencimentos") or 0.0),
                descontos=float(e.get("descontos") or 0.0),
            ))
        return out
    except Exception:
        return []


def parse_recibo_pagamento_pdf(pdf_path: str, use_gpt_fallback: bool = False, openai_model: str = "gpt-4.1") -> List[ReciboFolha]:
    recibos: List[ReciboFolha] = []
    with pdfplumber.open(pdf_path) as pdf:
        for idx, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if not text.strip():
                continue

            # Heurística: só tratar páginas que parecem holerite
            if "MENSALISTA" not in text.upper() and "VALOR LÍQUIDO" not in text.upper() and "VALOR LIQUIDO" not in text.upper():
                continue

            r = ReciboFolha(page_index=idx, raw_text=text)
            r.competencia = competencia_from_header(text)

            _extract_nome_cpf_line_flexible(text, r)
            _extract_cargo(text, r)
            _extract_matricula(text, r)

            totals = _extract_totais(text)
            r.total_venc = totals["total_venc"]
            r.total_desc = totals["total_desc"]
            r.liquido = totals["liquido"]

            eventos = _extract_eventos_from_table(page)
            if not eventos:
                eventos = _extract_eventos_regex(text)
            if not eventos and use_gpt_fallback:
                eventos = _gpt_extract_eventos(text, model=openai_model)

            r.eventos = eventos

            # Considera recibo válido se houver líquido ou eventos (mesmo que nome/CPF falhem)
            if (r.liquido is not None) or (r.eventos):
                recibos.append(r)

    return recibos
