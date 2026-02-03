from __future__ import annotations

import re
import unicodedata
from typing import Optional, Dict, Any, List, Tuple

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


def _norm_nome(s: Optional[str]) -> str:
    if not s:
        return ""
    s = str(s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^A-Za-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip().upper()
    return s


def _digits(s: Optional[str]) -> str:
    if not s:
        return ""
    return re.sub(r"\D+", "", str(s))


def _tokens(nome_norm: str) -> List[str]:
    return [t for t in (nome_norm or "").split(" ") if t]


def _score_name(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.92
    ta = set(_tokens(a))
    tb = set(_tokens(b))
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    jacc = inter / union if union else 0.0
    first_bonus = 0.0
    ta_list = _tokens(a)
    tb_list = _tokens(b)
    if ta_list and tb_list and ta_list[0] == tb_list[0]:
        first_bonus = 0.12
    return min(0.88 * jacc + first_bonus, 0.99)


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

    nome_col = _detect(df, ["NOME", "COLABORADOR", "FUNCIONARIO", "FUNCIONÁRIO"])
    bruto_col = _detect(df, ["BRUTO", "VALOR", "SALARIO", "SALÁRIO", "SALARIO REAL", "SALÁRIO REAL", "BRUTO REFERENCIAL"])
    cpf_col = _detect(df, ["CPF"])
    mat_col = _detect(df, ["MATRICULA", "MATRÍCULA", "MAT"])

    if bruto_col is None and df.shape[1] >= 2:
        bruto_col = df.columns[1]
    if nome_col is None and df.shape[1] >= 1:
        nome_col = df.columns[0]

    df["__NOME_COL__"] = df[nome_col].astype(str) if nome_col else ""
    df["NOME_NORM"] = df["__NOME_COL__"].map(_norm_nome)
    df["__BRUTO_COL__"] = df[bruto_col] if bruto_col else None

    df["CPF_DIG"] = df[cpf_col].map(_digits) if cpf_col else ""
    df["MAT_DIG"] = df[mat_col].map(_digits) if mat_col else ""

    return df


def top_candidates(df: pd.DataFrame, nome: str, k: int = 12) -> List[Tuple[int, float, str]]:
    a = _norm_nome(nome)
    out: List[Tuple[int, float, str]] = []
    if not a or "NOME_NORM" not in df.columns:
        return out
    for idx, b in df["NOME_NORM"].items():
        s = _score_name(a, str(b))
        if s > 0:
            out.append((idx, s, str(df.loc[idx, "__NOME_COL__"])))
    out.sort(key=lambda x: x[1], reverse=True)
    return out[:k]


def find_colaborador_ref(
    df: pd.DataFrame,
    cpf: Optional[str],
    matricula: Optional[str],
    nome: Optional[str],
    *,
    gpt_match_fn=None,
    gpt_min_score: float = 0.70,
    gpt_trigger_score: float = 0.82,
) -> Dict[str, Any]:
    status_col = _detect(df, ["STATUS", "SITUACAO", "SITUAÇÃO"])
    depto_col = _detect(df, ["DEPARTAMENTO", "DEPTO", "SETOR", "AREA", "ÁREA"])
    cargo_col = _detect(df, ["CARGO", "FUNCAO", "FUNÇÃO"])

    out = {
        "bruto_referencial": None,
        "status": None,
        "departamento": None,
        "cargo": None,
        "nome": None,
        "cpf": cpf,
        "matricula": matricula,
        "match_score": None,
        "match_nome_planilha": None,
        "match_metodo": None,
    }

    if df is None or len(df) == 0:
        return out

    cpf_d = _digits(cpf)
    mat_d = _digits(matricula)

    # CPF
    if cpf_d and "CPF_DIG" in df.columns:
        hit = df[df["CPF_DIG"] == cpf_d]
        if len(hit) == 1:
            r = hit.iloc[0]
            out["bruto_referencial"] = _to_float(r.get("__BRUTO_COL__"))
            out["status"] = str(r[status_col]).strip() if status_col and status_col in hit.columns else None
            out["departamento"] = str(r[depto_col]).strip() if depto_col and depto_col in hit.columns else None
            out["cargo"] = str(r[cargo_col]).strip() if cargo_col and cargo_col in hit.columns else None
            out["nome"] = str(r.get("__NOME_COL__", "")).strip().title() or nome
            out["match_score"] = 1.0
            out["match_nome_planilha"] = str(r.get("__NOME_COL__", "")).strip()
            out["match_metodo"] = "cpf"
            return out

    # Matrícula
    if mat_d and "MAT_DIG" in df.columns:
        hit = df[df["MAT_DIG"] == mat_d]
        if len(hit) == 1:
            r = hit.iloc[0]
            out["bruto_referencial"] = _to_float(r.get("__BRUTO_COL__"))
            out["status"] = str(r[status_col]).strip() if status_col and status_col in hit.columns else None
            out["departamento"] = str(r[depto_col]).strip() if depto_col and depto_col in hit.columns else None
            out["cargo"] = str(r[cargo_col]).strip() if cargo_col and cargo_col in hit.columns else None
            out["nome"] = str(r.get("__NOME_COL__", "")).strip().title() or nome
            out["match_score"] = 1.0
            out["match_nome_planilha"] = str(r.get("__NOME_COL__", "")).strip()
            out["match_metodo"] = "matricula"
            return out

    # Nome fuzzy + GPT
    a = _norm_nome(nome)
    best = None
    for idx, b in df["NOME_NORM"].items():
        s = _score_name(a, str(b))
        if best is None or s > best[1]:
            best = (idx, s, str(df.loc[idx, "__NOME_COL__"]))

    if best is None or best[1] < gpt_min_score:
        return out

    idx, score, nome_plan = best
    metodo = "local_fuzzy"

    if gpt_match_fn is not None and score < gpt_trigger_score:
        cands = top_candidates(df, nome or "", k=12)
        cand_names = [c[2] for c in cands]
        picked = gpt_match_fn(nome or "", cand_names)
        if picked:
            for cidx, _, cname in cands:
                if str(cname).strip().upper() == str(picked).strip().upper():
                    idx, score, nome_plan = cidx, max(score, 0.95), cname
                    metodo = "gpt_disambiguation"
                    break

    r = df.loc[idx]
    out["bruto_referencial"] = _to_float(r.get("__BRUTO_COL__"))
    out["status"] = str(r[status_col]).strip() if status_col and status_col in df.columns else None
    out["departamento"] = str(r[depto_col]).strip() if depto_col and depto_col in df.columns else None
    out["cargo"] = str(r[cargo_col]).strip() if cargo_col and cargo_col in df.columns else None
    out["nome"] = str(r.get("__NOME_COL__", "")).strip().title() or nome
    out["match_score"] = float(score)
    out["match_nome_planilha"] = str(nome_plan).strip()
    out["match_metodo"] = metodo

    return out
