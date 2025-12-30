from __future__ import annotations

import re
from typing import Optional, Dict, Any
import pandas as pd

def _detect_column(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    cols = {str(c).strip().upper(): c for c in df.columns}
    for cand in candidates:
        if cand.upper() in cols:
            return cols[cand.upper()]
    for uc, orig in cols.items():
        for cand in candidates:
            if cand.upper() in uc:
                return orig
    return None

def cpf_digits(cpf: Optional[str]) -> str:
    return re.sub(r"\D", "", str(cpf or ""))

def _to_float(v) -> Optional[float]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    t = str(v).strip().replace("\xa0"," ").replace(" ", "")
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
    # normalizar CPF se existir
    cpf_col = _detect_column(df, ["CPF"])
    if cpf_col:
        df["CPF_DIGITS"] = df[cpf_col].map(cpf_digits)
    else:
        df["CPF_DIGITS"] = ""
    return df

def find_colaborador_ref(df: pd.DataFrame, cpf: Optional[str], nome: Optional[str]) -> Dict[str, Any]:
    """
    Retorna um dicionário com:
      bruto_referencial, status, departamento, cargo
    Faz match por CPF (preferencial) e fallback por nome (exato).
    """
    cpf_col = _detect_column(df, ["CPF"])
    nome_col = _detect_column(df, ["NOME", "COLABORADOR", "FUNCIONARIO", "FUNCIONÁRIO"])
    bruto_col = _detect_column(df, ["BRUTO", "SALARIO", "SALÁRIO", "SALARIO REAL", "SALÁRIO REAL", "BRUTO REFERENCIAL"])
    status_col = _detect_column(df, ["STATUS", "SITUAÇÃO", "SITUACAO", "ATIVO"])
    depto_col = _detect_column(df, ["DEPARTAMENTO", "DEPTO", "SETOR", "AREA", "ÁREA"])
    cargo_col = _detect_column(df, ["CARGO", "FUNCAO", "FUNÇÃO"])

    hit = None
    if cpf and "CPF_DIGITS" in df.columns:
        c = cpf_digits(cpf)
        if c:
            hit = df[df["CPF_DIGITS"] == c]
            if len(hit) == 0 and cpf_col:
                hit = df[df[cpf_col].astype(str).map(cpf_digits) == c]

    if (hit is None or len(hit) == 0) and nome and nome_col:
        nn = re.sub(r"\s+", " ", str(nome)).strip().upper()
        ser = df[nome_col].astype(str).map(lambda x: re.sub(r"\s+", " ", str(x)).strip().upper())
        hit = df[ser == nn]

    out = {"bruto_referencial": None, "status": None, "departamento": None, "cargo": None}
    if hit is not None and len(hit) >= 1:
        row = hit.iloc[0]
        out["bruto_referencial"] = _to_float(row[bruto_col]) if bruto_col else None
        out["status"] = str(row[status_col]).strip() if status_col else None
        out["departamento"] = str(row[depto_col]).strip() if depto_col else None
        out["cargo"] = str(row[cargo_col]).strip() if cargo_col else None

    return out
