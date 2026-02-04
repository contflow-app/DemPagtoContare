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


def norm_nome(s: Optional[str]) -> str:
    """Normaliza nome para conciliação: sem acentos, maiúsculo, remove pontuação."""
    if not s:
        return ""
    s = str(s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^A-Za-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip().upper()
    return s


def _tokens(s: str) -> List[str]:
    return [t for t in s.split() if t]


def _score(a: str, b: str) -> float:
    """Score 0..1 por overlap de tokens + bônus por primeiro/último nome."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.93

    ta = _tokens(a)
    tb = _tokens(b)
    sa, sb = set(ta), set(tb)
    inter = len(sa & sb)
    union = len(sa | sb) or 1
    j = inter / union

    bonus = 0.0
    if ta and tb and ta[0] == tb[0]:
        bonus += 0.10
    if ta and tb and ta[-1] == tb[-1]:
        bonus += 0.10

    return min(0.85 * j + bonus, 0.99)


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
    """Planilha mínima: Nome + Valor/Bruto. Outras colunas são opcionais."""
    df = pd.read_excel(path, dtype=str)

    nome_col = _detect(df, ["NOME", "COLABORADOR", "FUNCIONARIO", "FUNCIONÁRIO"])
    bruto_col = _detect(df, ["BRUTO", "VALOR", "SALARIO", "SALÁRIO", "SALARIO REAL", "SALÁRIO REAL", "BRUTO REFERENCIAL"])

    if bruto_col is None and df.shape[1] >= 2:
        bruto_col = df.columns[1]
    if nome_col is None and df.shape[1] >= 1:
        nome_col = df.columns[0]

    df["__NOME_COL__"] = df[nome_col].astype(str) if nome_col else ""
    df["NOME_NORM"] = df["__NOME_COL__"].map(norm_nome)
    df["__BRUTO_COL__"] = df[bruto_col] if bruto_col else None

    # opcionais
    status_col = _detect(df, ["STATUS", "SITUACAO", "SITUAÇÃO"])
    depto_col = _detect(df, ["DEPARTAMENTO", "DEPTO", "SETOR", "AREA", "ÁREA"])
    cargo_col = _detect(df, ["CARGO", "FUNCAO", "FUNÇÃO"])
    df["__STATUS__"] = df[status_col] if status_col else None
    df["__DEPTO__"] = df[depto_col] if depto_col else None
    df["__CARGO__"] = df[cargo_col] if cargo_col else None

    return df


def _top_candidates(df: pd.DataFrame, nome_holerite: str, k: int = 12) -> List[Tuple[int, float, str]]:
    a = norm_nome(nome_holerite)
    cands: List[Tuple[int, float, str]] = []
    for idx, b in df["NOME_NORM"].items():
        s = _score(a, str(b))
        if s > 0:
            cands.append((idx, s, str(df.loc[idx, "__NOME_COL__"])))
    cands.sort(key=lambda x: x[1], reverse=True)
    return cands[:k]


def find_colaborador_ref(
    df: pd.DataFrame,
    nome: Optional[str],
    *,
    gpt_match_fn=None,
    min_score: float = 0.70,
    force_gpt_below: float = 0.95,
) -> Dict[str, Any]:
    """Identificação APENAS por nome. GPT desambigua quando não é match perfeito."""
    out = {
        "bruto_referencial": None,
        "status": None,
        "departamento": None,
        "cargo": None,
        "nome": None,
        "match_score": None,
        "match_nome_planilha": None,
        "match_metodo": None,
    }
    if df is None or len(df) == 0:
        return out

    a = norm_nome(nome)

    exact = df[df["NOME_NORM"] == a]
    if len(exact) == 1:
        r = exact.iloc[0]
        out.update({
            "bruto_referencial": _to_float(r.get("__BRUTO_COL__")),
            "status": (str(r.get("__STATUS__")).strip() if r.get("__STATUS__") is not None else None),
            "departamento": (str(r.get("__DEPTO__")).strip() if r.get("__DEPTO__") is not None else None),
            "cargo": (str(r.get("__CARGO__")).strip() if r.get("__CARGO__") is not None else None),
            "nome": str(r.get("__NOME_COL__", "")).strip().title() or nome,
            "match_score": 1.0,
            "match_nome_planilha": str(r.get("__NOME_COL__", "")).strip(),
            "match_metodo": "nome_exato",
        })
        return out

    cands = _top_candidates(df, nome or "", k=12)
    if not cands or cands[0][1] < min_score:
        return out

    best_idx, best_score, best_nome = cands[0]
    metodo = "nome_fuzzy"

    if gpt_match_fn is not None and (best_score < force_gpt_below or (len(cands) > 1 and cands[1][1] >= best_score - 0.04)):
        picked = gpt_match_fn(nome or "", [c[2] for c in cands])
        if picked:
            for idx, sc, nm in cands:
                if str(nm).strip().upper() == str(picked).strip().upper():
                    best_idx, best_score, best_nome = idx, max(best_score, 0.97), nm
                    metodo = "gpt_disambiguation"
                    break

    r = df.loc[best_idx]
    out.update({
        "bruto_referencial": _to_float(r.get("__BRUTO_COL__")),
        "status": (str(r.get("__STATUS__")).strip() if r.get("__STATUS__") is not None else None),
        "departamento": (str(r.get("__DEPTO__")).strip() if r.get("__DEPTO__") is not None else None),
        "cargo": (str(r.get("__CARGO__")).strip() if r.get("__CARGO__") is not None else None),
        "nome": str(r.get("__NOME_COL__", "")).strip().title() or nome,
        "match_score": float(best_score),
        "match_nome_planilha": str(best_nome).strip(),
        "match_metodo": metodo,
    })
    return out
