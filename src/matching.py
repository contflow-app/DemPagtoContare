from __future__ import annotations

import re
import unicodedata
from typing import Optional, Dict, Any

import pandas as pd


def _detect(df: pd.DataFrame, names: list[str]) -> Optional[str]:
    cols = {str(c).strip().upper(): c for c in df.columns}
    for n in names:
        if n.upper() in cols:
            return cols[n.upper()]
    # tentativa por "contém"
    for uc, orig in cols.items():
        for n in names:
            if n.upper() in uc:
                return orig
    return None


def _norm_nome(s: Optional[str]) -> str:
    """Normaliza nome para comparação: maiúsculo, sem acentos, espaços colapsados."""
    if not s:
        return ""
    s = str(s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^A-Za-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip().upper()
    return s


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
    """Lê planilha com colunas mínimas: NOME e VALOR/BRUTO (sem CPF)."""
    df = pd.read_excel(path, dtype=str)

    # Detecta colunas
    nome_col = _detect(df, ["NOME", "COLABORADOR", "FUNCIONARIO", "FUNCIONÁRIO"])
    bruto_col = _detect(df, ["BRUTO", "VALOR", "SALARIO", "SALÁRIO", "SALARIO REAL", "SALÁRIO REAL", "BRUTO REFERENCIAL"])

    # Se não achar 'bruto', usa a 2ª coluna (muito comum: [NOME, VALOR])
    if bruto_col is None and df.shape[1] >= 2:
        bruto_col = df.columns[1]

    # Se não achar 'nome', usa a 1ª coluna
    if nome_col is None and df.shape[1] >= 1:
        nome_col = df.columns[0]

    df["__NOME_COL__"] = df[nome_col].astype(str) if nome_col else ""
    df["NOME_NORM"] = df["__NOME_COL__"].map(_norm_nome)
    df["__BRUTO_COL__"] = df[bruto_col] if bruto_col else None

    return df


def find_colaborador_ref(df: pd.DataFrame, cpf: Optional[str], nome: Optional[str]) -> Dict[str, Any]:
    """Encontra colaborador por NOME (CPF opcional; pode não existir na planilha)."""
    # detecta colunas opcionais
    status_col = _detect(df, ["STATUS", "SITUACAO", "SITUAÇÃO"])
    depto_col = _detect(df, ["DEPARTAMENTO", "DEPTO", "SETOR", "AREA", "ÁREA"])
    cargo_col = _detect(df, ["CARGO", "FUNCAO", "FUNÇÃO"])

    out = {
        "bruto_referencial": None,
        "status": None,
        "departamento": None,
        "cargo": None,
        "nome": None,
        "cpf": cpf,  # mantém o CPF vindo do holerite (se existir)
    }

    if df is None or len(df) == 0:
        return out

    nome_norm = _norm_nome(nome)

    hit = None
    if nome_norm and "NOME_NORM" in df.columns:
        hit = df[df["NOME_NORM"] == nome_norm]

        # fallback: contém (quando o holerite traz nome com 2 sobrenomes e a planilha abrevia, etc.)
        if (hit is None or len(hit) == 0) and nome_norm:
            hit = df[df["NOME_NORM"].astype(str).str.contains(nome_norm, na=False)]

    if hit is not None and len(hit) >= 1:
        r = hit.iloc[0]
        out["bruto_referencial"] = _to_float(r.get("__BRUTO_COL__"))
        out["status"] = str(r[status_col]).strip() if status_col and status_col in hit.columns else None
        out["departamento"] = str(r[depto_col]).strip() if depto_col and depto_col in hit.columns else None
        out["cargo"] = str(r[cargo_col]).strip() if cargo_col and cargo_col in hit.columns else None
        out["nome"] = str(r.get("__NOME_COL__", "")).strip().title() if str(r.get("__NOME_COL__", "")).strip() else nome

    return out
