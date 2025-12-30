from __future__ import annotations
from typing import Optional

def infer_familia(texto: str) -> str:
    t = (texto or "").lower()
    if any(k in t for k in [" dp", "departamento pessoal", "pessoal", "folha"]):
        return "DP"
    if any(k in t for k in ["fiscal", "tribut", "imposto"]):
        return "Fiscal"
    if any(k in t for k in ["contábil", "contabil", "balanço", "balanco"]):
        return "Contábil"
    if any(k in t for k in ["ti", "suporte", "tecnologia"]):
        return "TI"
    return "Geral"

def nivel_por_salario(bruto: Optional[float]) -> Optional[str]:
    if bruto is None:
        return None
    b = float(bruto)
    if b < 2500: return "Assistente I"
    if b < 3500: return "Assistente II"
    if b < 5000: return "Analista Jr"
    if b < 7000: return "Analista Pl"
    return "Analista Sr"

def cargo_final(familia: str, nivel: Optional[str]) -> str:
    fam = familia or "Geral"
    niv = nivel or "Nível"
    return f"{niv} {fam}"
