from __future__ import annotations
import re
from typing import Optional, Dict, Any
import pandas as pd

def _detect(df: pd.DataFrame, names: list[str]) -> Optional[str]:
    cols = {str(c).strip().upper(): c for c in df.columns}
    for n in names:
        if n.upper() in cols:
            return cols[n.upper()]
    for uc, orig in cols.items():
        for n in names:
            if n.upper() in uc:
                return orig
    return None

def cpf_digits(x: Optional[str]) -> str:
    return re.sub(r"\D", "", str(x or ""))

def _to_float(v) -> Optional[float]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    t = str(v).strip()
    t = re.sub(r"[^0-9\.,\-]", "", t)
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except Exception:
        return None

def load_salario_real_xlsx(path: str) -> pd.DataFrame:
    df = pd.read_excel(path, dtype=str)
    cpf_col = _detect(df, ["CPF"])
    df["CPF_DIGITS"] = df[cpf_col].map(cpf_digits) if cpf_col else ""
    return df

def find_colaborador_ref(df: pd.DataFrame, cpf: Optional[str], nome: Optional[str]) -> Dict[str, Any]:
    cpf_col = _detect(df, ["CPF"])
    nome_col = _detect(df, ["NOME", "COLABORADOR", "FUNCIONARIO", "FUNCIONÁRIO"])
    bruto_col = _detect(df, ["BRUTO", "SALARIO", "SALÁRIO", "SALARIO REAL", "SALÁRIO REAL", "BRUTO REFERENCIAL"])
    status_col = _detect(df, ["STATUS", "SITUACAO", "SITUAÇÃO"])
    depto_col = _detect(df, ["DEPARTAMENTO", "DEPTO", "SETOR", "AREA", "ÁREA"])
    cargo_col = _detect(df, ["CARGO", "FUNCAO", "FUNÇÃO"])

    out = {"bruto_referencial": None, "status": None, "departamento": None, "cargo": None, "nome": None, "cpf": None}

    hit = None
    cpf_norm = cpf_digits(cpf) if cpf else ""
    if cpf_norm and "CPF_DIGITS" in df.columns:
        hit = df[df["CPF_DIGITS"] == cpf_norm]

    if (hit is None or len(hit) == 0) and nome and nome_col:
        nn = re.sub(r"\s+", " ", str(nome)).strip().upper()
        ser = df[nome_col].astype(str).map(lambda x: re.sub(r"\s+", " ", str(x)).strip().upper())
        hit = df[ser == nn]

    if hit is not None and len(hit) >= 1:
        r = hit.iloc[0]
        out["bruto_referencial"] = _to_float(r[bruto_col]) if bruto_col else None
        out["status"] = str(r[status_col]).strip() if status_col else None
        out["departamento"] = str(r[depto_col]).strip() if depto_col else None
        out["cargo"] = str(r[cargo_col]).strip() if cargo_col else None
        if nome_col:
            out["nome"] = str(r[nome_col]).strip().title() if str(r[nome_col]).strip() else None
        if cpf_col:
            out["cpf"] = str(r[cpf_col]).strip() if str(r[cpf_col]).strip() else None
    return out
