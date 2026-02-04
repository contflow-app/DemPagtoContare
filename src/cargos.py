from __future__ import annotations
from typing import Optional

def infer_familia(texto: str) -> str:
    t = (texto or "").lower()
    if "fiscal" in t: return "Fiscal"
    if "dp" in t or "pessoal" in t or "folha" in t: return "DP"
    if "contab" in t: return "Contábil"
    return "Geral"

def nivel_por_salario(bruto: Optional[float]) -> Optional[str]:
    if bruto is None: return None
    b = float(bruto)
    if b < 2500: return "Assistente I"
    if b < 3500: return "Assistente II"
    if b < 5000: return "Analista Jr"
    if b < 7000: return "Analista Pl"
    return "Analista Sr"

def cargo_final(familia: str, nivel: Optional[str]) -> str:
    return f"{nivel} {familia or 'Geral'}" if nivel else (familia or 'Geral')
