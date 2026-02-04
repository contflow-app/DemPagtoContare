from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

import pdfplumber

# Regex de dinheiro pt-BR (1.234,56)
MONEY_RE = re.compile(r"(?:\d{1,3}(?:\.\d{3})*|\d+),(?:\d{2})")
CPF_RE = re.compile(r"\b(\d{3}\.\d{3}\.\d{3}-\d{2})\b")


# Mapeamento de códigos mais comuns do holerite (Contare) -> tipo
# 'P' = provento, 'D' = desconto
CODE_HINT = {
    "8781": "P",  # salário contratual (CLT) - usado só para referência/base dias
    "8786": "P",  # dias afast. p/acid trabalho (exemplo)
    "8808": "D",  # desconto dias afastados acid trabalho (exemplo Paula)
    "250": "P",   # reflexo extras dsr
    "854": "P",   # reflexo adic noturno dsr
    "150": "P",   # horas extras
    "687": "P",   # horas extras home office
    "25": "P",    # adicional noturno
    "940": "P",   # dif. férias
    "995": "P",   # salário família
    "8112": "P",  # dif. 1/3 de férias
    "8189": "P",  # dif. média hora férias
    "990": "P",   # estouro do mês (pode vir como provento)
    "991": "D",   # estouro mês anterior (frequente como desconto)
    "240": "D",   # vale (adiantamento)
    "998": "D",   # INSS
    "821": "D",   # INSS diferença férias
    "981": "D",   # desc adiantamento salarial
    "686": "D",   # desc curso
    "8069": "D",  # atrasos/horas faltas
    "681": "D",   # desconto processo judicial
    "255": "D",   # ressarcimento prejuízo
}
DISCOUNT_KEYWORDS = (
    "INSS", "I.N.S.S", "DESC", "DESCONTO", "ATRAS", "FALTA", "MULTA", "PENAL",
    "RESSARC", "PREJUI", "ADIANT", "VALE", "PROCESSO",
)

_MES_MAP = {
    "JANEIRO": "01",
    "FEVEREIRO": "02",
    "MARCO": "03",
    "MARÇO": "03",
    "ABRIL": "04",
    "MAIO": "05",
    "JUNHO": "06",
    "JULHO": "07",
    "AGOSTO": "08",
    "SETEMBRO": "09",
    "OUTUBRO": "10",
    "NOVEMBRO": "11",
    "DEZEMBRO": "12",
}


def parse_money_any(x) -> Optional[float]:
    """Converte moeda pt-BR para float. Aceita float/int/string."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if not s:
        return None
    s = re.sub(r"[^0-9,\.\-]", "", s)
    if not s:
        return None
    # se tem vírgula, assume decimal pt-BR
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def _extract_cpf(text: str) -> Optional[str]:
    m = CPF_RE.search(text or "")
    return m.group(1) if m else None



def _extract_totais(text: str) -> Tuple[Optional[float], Optional[float]]:
    """Extrai Total de Vencimentos e Total de Descontos (quando presentes no texto)."""
    text = text or ""
    tv = None
    td = None
    mv = re.search(r"Total\s+de\s+Vencimentos\s+(" + MONEY_RE.pattern + r")", text, flags=re.IGNORECASE)
    md = re.search(r"Total\s+de\s+Descontos\s+(" + MONEY_RE.pattern + r")", text, flags=re.IGNORECASE)
    if mv:
        tv = parse_money_any(mv.group(1))
    if md:
        td = parse_money_any(md.group(1))
    return tv, td


def _regex_guess(text: str) -> Dict:
    """Extração determinística para holerites Contare (CNPJ + Mensalista + tabela de verbas)."""
    text = text or ""

    cpf = _extract_cpf(text)

    # Competência por 'Mensalista <MÊS> de <AAAA>' (evita capturar 'Admissão dd/mm/aaaa')
    comp = None
    m1 = re.search(r"Mensalista\s+([A-Za-zÇçÁÉÍÓÚÂÊÔÃÕÀ-Üà-ü]+)\s+de\s+(\d{4})", text, flags=re.IGNORECASE)
    if m1:
        mes = m1.group(1).strip().upper()
        mes = (mes.replace("Ç","C").replace("Ã","A").replace("Á","A").replace("Â","A").replace("À","A")
                  .replace("É","E").replace("Ê","E").replace("Í","I").replace("Ó","O").replace("Ô","O")
                  .replace("Õ","O").replace("Ú","U"))
        ano = m1.group(2)
        mm = _MES_MAP.get(mes)
        if mm:
            comp = f"{mm}/{ano}"

    # Nome pela linha após "Código Nome do Funcionário"
    nome = None
    m2 = re.search(
        r"C[oó]digo\s+Nome\s+do\s+Funcion[aá]rio[\s\S]{0,250}?(?:\n|\r\n)\s*\d+\s+([A-ZÇÁÉÍÓÚÂÊÔÃÕÀ-Ü ]{5,80}?)\s+\d{6}\b",
        text,
        flags=re.IGNORECASE,
    )
    if m2:
        nome = m2.group(1).strip().title()

    # Líquido
    liquido = None
    mliq = re.search(rf"Valor\s+L[ií]quido\s*(?:[:\-])?\s*({MONEY_RE.pattern})", text, flags=re.IGNORECASE)
    if mliq:
        liquido = parse_money_any(mliq.group(1))

    # Eventos
    eventos: List[Dict] = []
    in_table = False
    done_first_copy = False
    for ln in text.splitlines():
        s = " ".join(ln.split())
        if not s:
            continue
        if re.search(r"C[oó]digo\s+Descri[cç][aã]o\s+Refer[eê]ncia\s+Vencimentos\s+Descontos", s, flags=re.IGNORECASE):
            if done_first_copy:
                break
            in_table = True
            continue
        if in_table and re.search(r"Total\s+de\s+Vencimentos|Total\s+de\s+Descontos|Valor\s+L[ií]quido", s, flags=re.IGNORECASE):
            in_table = False
            if eventos:
                done_first_copy = True
            continue
        if in_table:
            # Parsing robusto por tokens (suporta referência como 30,00 / 7,54 / 5:38 etc).
            toks = s.split()
            if len(toks) < 4:
                continue
            codigo = toks[0]

            # helper: identifica token de referência (tempo 5:38 ou número com vírgula)
            def _is_ref_token(tok: str) -> bool:
                if re.fullmatch(r"\d{1,2}:\d{2}", tok or ""):
                    return True
                if MONEY_RE.fullmatch(tok or ""):
                    return True
                return False

            # pega valores monetários do final (até 2 colunas: vencimentos e descontos)
            tail_money_str = []
            while len(toks) > 1 and MONEY_RE.fullmatch(toks[-1]):
                tail_money_str.append(toks.pop(-1))
                if len(tail_money_str) == 2:
                    break
            tail_money_str = list(reversed(tail_money_str))
            tail_money = [parse_money_any(x) for x in tail_money_str]

            if not tail_money:
                continue

            # referência é o último token restante (pode ser 30,00 / 5:38 / 7,54 / 224,42 ou texto)
            referencia = toks[-1] if len(toks) > 1 else None

            # Se referência NÃO parece token de referência (ex.: "CONTRATUAL."), e há 2 dinheiros no fim,
            # então o primeiro dinheiro é na verdade a referência e só existe 1 coluna de valor (o último dinheiro).
            # Ex.: "... 30,00 1.621,00"  => ref=30,00, valor=1.621,00
            one_value_mode = False
            if referencia is not None and not _is_ref_token(referencia) and len(tail_money) == 2:
                referencia = tail_money_str[0]
                tail_money = [tail_money[1]]
                one_value_mode = True

            # remove referência dos tokens para formar descrição
            if referencia is not None and len(toks) > 1:
                toks = toks[:-1]

            descricao = " ".join(toks[1:]).strip()

            desc_up = descricao.upper()
            desconto_hint = (
                desc_up.startswith("DESC")
                or "INSS" in desc_up or "I.N.S.S" in desc_up
                or "RESSARC" in desc_up or "PREJUI" in desc_up
                or "ATRAS" in desc_up or "FALTA" in desc_up
                or "MULTA" in desc_up or "PENAL" in desc_up
                or codigo in {"981","998","686","255","8069"}
            )

            provento = None
            desconto = None

            if len(tail_money) == 1:
                val = tail_money[0]
                if desconto_hint:
                    desconto = val if (val or 0) != 0 else None
                else:
                    provento = val if (val or 0) != 0 else None
            else:
                # 2 colunas monetárias (vencimentos + descontos)
                if desconto_hint:
                    # em descontos, usar a última coluna como desconto
                    desconto = tail_money[-1] if (tail_money[-1] or 0) != 0 else None
                else:
                    provento = tail_money[0] if (tail_money[0] or 0) != 0 else None
                    desconto = tail_money[1] if (tail_money[1] or 0) != 0 else None

            eventos.append({
                "codigo": codigo,
                "descricao": descricao,
                "referencia": referencia,
                "provento": provento,
                "desconto": desconto,
            })

    # dedup simples
    seen = set()
    dedup = []
    for e in eventos:
        key = (e.get('codigo'), e.get('descricao'), e.get('referencia'), e.get('provento'), e.get('desconto'))
        if key in seen:
            continue
        seen.add(key)
        dedup.append(e)

    tv, td = _extract_totais(text)

    return {"competencia": comp, "nome": nome, "cpf": cpf, "liquido": liquido, "total_vencimentos": tv, "total_descontos": td, "eventos": dedup}


def _gpt_extract(text: str, model: str) -> Dict:
    """GPT primário para extração e classificação de eventos.

    Estratégia:
    - Extrai competência, nome, CPF (se houver), totais (vencimentos/descontos) e lista de eventos.
    - Para cada evento, devolve explicitamente provento e/ou desconto (number ou null).
    - Usa os totais como restrição de consistência (quando presentes).
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {}

    try:
        from openai import OpenAI
        import json
    except Exception:
        return {}

    client = OpenAI(api_key=api_key)

    rules = {
        "codigos_desconto": sorted(list({ "255","981","998","686","8069","681","240","821","8808" })),
        "codigos_provento": sorted(list({ "8781","8786","250","854","150","25","687","990","995","8112","8189","940" })),
        "palavras_desconto": ["INSS","I.N.S.S","DESC","DESCONTO","ADIANT","VALE","ATRAS","FALTA","RESSARC","PREJUI","PROCESSO","MULTA","PENAL"],
    }

    system = (
        "Você extrai dados de holerites brasileiros. Responda APENAS JSON válido. "
        "Não invente; se não encontrar no texto, use null. "
        "Classifique corretamente proventos e descontos."
    )

    user = {
        "tarefa": "Extrair holerite",
        "regras_classificacao": rules,
        "observacao": (
            "Se um evento tiver somente 1 valor monetário na linha, decida se é provento ou desconto "
            "pelas regras (código/palavras) e pelo contexto dos totais. "
            "Se houver 'Total de Vencimentos' e 'Total de Descontos' no texto, use como checagem: "
            "a soma dos proventos deve se aproximar do total de vencimentos e a soma dos descontos do total de descontos."
        ),
        "saida_json": {
                "competencia": "MM/AAAA ou null",
                "nome": "string ou null",
                "cpf": "000.000.000-00 ou null",
                "total_vencimentos": "number ou null",
                "total_descontos": "number ou null",
            "valor_liquido": "number ou null",
                "eventos": [
                {"codigo": "string", "descricao": "string", "referencia": "string|null", "provento": "number|null", "desconto": "number|null"}
            ],
        },
        "texto": (text or "")[:14000],
    }

    try:
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.output_text or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _gpt_refine_events(text: str, model: str, base: Dict, attempt_note: str) -> Dict:
    """Segunda passada GPT quando a reconciliação por totais falhar."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {}
    try:
        from openai import OpenAI
        import json
    except Exception:
        return {}

    client = OpenAI(api_key=api_key)
    system = (
        "Você corrige uma extração de holerite. Responda APENAS JSON. "
        "Ajuste somente a classificação provento/desconto e referências/valores se necessário, "
        "para bater com os totais informados."
    )
    payload = {
        "nota": attempt_note,
        "texto": (text or "")[:14000],
        "extraido_atual": {
                "total_vencimentos": base.get("total_vencimentos"),
                "total_descontos": base.get("total_descontos"),
                "eventos": base.get("eventos"),
        },
        "regras": {
            "descontos": ["255","981","998","686","8069","681","240","821","8808"],
            "proventos": ["8781","8786","250","854","150","25","687","990","995","8112","8189","940"],
        },
        "saida_json": {
                "total_vencimentos": "number ou null",
                "total_descontos": "number ou null",
                "eventos": [
                {"codigo": "string", "descricao": "string", "referencia": "string|null", "provento": "number|null", "desconto": "number|null"}
            ],
        },
    }
    try:
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.output_text or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}





def _post_process_eventos(eventos: List[Dict], total_venc: Optional[float] = None, total_desc: Optional[float] = None) -> List[Dict]:
    """Normaliza classificação provento/desconto e tenta reconciliar com totais do holerite.

    Regras:
    - Força descontos por códigos e palavras-chave conhecidas.
    - Se houver totais (Total de Vencimentos/Descontos), tenta realocar itens ambíguos para minimizar erro.
    """
    if not eventos:
        return eventos

    # 'P' provento, 'D' desconto
    code_hint = globals().get("CODE_HINT", {})
    discount_keywords = tuple(globals().get("DISCOUNT_KEYWORDS", ())) or (
        "INSS","I.N.S.S","DESC","DESCONTO","ADIANT","VALE","ATRAS","FALTA","RESSARC","PREJUI","PROCESSO","MULTA","PENAL"
    )

    def is_discount(codigo: str, desc_up: str) -> bool:
        hint = code_hint.get(codigo)
        if hint == "D":
            return True
        if hint == "P":
            return False
        return any(k in desc_up for k in discount_keywords)

    out: List[Dict] = []
    ambiguous_idx: List[int] = []

    for e in eventos:
        codigo = str(e.get("codigo") or "").strip()
        desc = str(e.get("descricao") or "").strip()
        desc_up = desc.upper()

        provento = e.get("provento")
        desconto = e.get("desconto")

        # Se veio só um valor (em provento) mas é desconto, move.
        if provento is not None and desconto is None and is_discount(codigo, desc_up):
            desconto = provento
            provento = None

        # Se veio só em desconto mas o código é provento, move (caso raro)
        if desconto is not None and provento is None and code_hint.get(codigo) == "P":
            provento = desconto
            desconto = None

        # Se ambos preenchidos e é desconto, zera provento
        if provento is not None and desconto is not None and is_discount(codigo, desc_up):
            provento = None

        if provento == 0:
            provento = None
        if desconto == 0:
            desconto = None

        if (provento is not None) ^ (desconto is not None):
            if codigo not in code_hint and not any(k in desc_up for k in discount_keywords):
                ambiguous_idx.append(len(out))

        out.append({**e, "provento": provento, "desconto": desconto})

    if total_venc is not None or total_desc is not None:
        sv = sum((e.get("provento") or 0) for e in out)
        sd = sum((e.get("desconto") or 0) for e in out)

        def objective(sv, sd):
            ev = abs(total_venc - sv) if total_venc is not None else 0.0
            ed = abs(total_desc - sd) if total_desc is not None else 0.0
            return ev + ed

        best = objective(sv, sd)
        improved = True
        while improved:
            improved = False
            for idx in ambiguous_idx:
                e = out[idx]
                pv = e.get("provento")
                dc = e.get("desconto")
                if pv is not None and dc is None:
                    cand_sv, cand_sd = sv - pv, sd + pv
                    cand = objective(cand_sv, cand_sd)
                    if cand + 0.01 < best:
                        out[idx] = {**e, "provento": None, "desconto": pv}
                        sv, sd, best = cand_sv, cand_sd, cand
                        improved = True
                elif dc is not None and pv is None:
                    cand_sv, cand_sd = sv + dc, sd - dc
                    cand = objective(cand_sv, cand_sd)
                    if cand + 0.01 < best:
                        out[idx] = {**e, "provento": dc, "desconto": None}
                        sv, sd, best = cand_sv, cand_sd, cand
                        improved = True

    return out


def parse_recibo_pagamento_pdf(pdf_path: str, use_gpt: bool = True, openai_model: str = "gpt-4.1") -> Tuple[List[Dict], Optional[str]]:
    results: List[Dict] = []
    competencia_global: Optional[str] = None

    with pdfplumber.open(pdf_path) as pdf:
        for idx, page in enumerate(pdf.pages):
            txt = page.extract_text() or ""

            base = _regex_guess(txt)
            data = _gpt_extract(txt, openai_model) if use_gpt else {}

            # merge: GPT só sobrescreve quando traz valor útil
            comp = data.get("competencia") or base.get("competencia")
            nome = data.get("nome") or base.get("nome")
            cpf = data.get("cpf") or base.get("cpf")

            tv = data.get("total_vencimentos")
            td = data.get("total_descontos")
            if tv is None:
                tv = base.get("total_vencimentos")
            if td is None:
                td = base.get("total_descontos")

            liquido = data.get("valor_liquido")
            if liquido is None:
                liquido = base.get("liquido")

            eventos = data.get("eventos")
            if not isinstance(eventos, list) or len(eventos) == 0:
                eventos = base.get("eventos") or []

            # pós-processamento + reconciliação com totais (se existirem)
            eventos = _post_process_eventos(eventos, tv, td)

            def _sum(evts):
                sv = sum((e.get("provento") or 0) for e in evts)
                sd = sum((e.get("desconto") or 0) for e in evts)
                return sv, sd

            # Se há totais e o erro é grande, tenta 1 refinamento GPT focado em bater totals.
            if use_gpt and (tv is not None or td is not None):
                sv, sd = _sum(eventos)
                mismatch = 0.0
                if tv is not None:
                    mismatch += abs(tv - sv)
                if td is not None:
                    mismatch += abs(td - sd)

                if mismatch > 2.0:
                    refined = _gpt_refine_events(
                        txt,
                        openai_model,
                        {"total_vencimentos": tv, "total_descontos": td, "eventos": eventos},
                        attempt_note=f"mismatch={mismatch:.2f} (sv={sv:.2f}, sd={sd:.2f}, tv={tv}, td={td})",
                    )
                    if isinstance(refined, dict) and isinstance(refined.get("eventos"), list) and refined.get("eventos"):
                        tv2 = refined.get("total_vencimentos") or tv
                        td2 = refined.get("total_descontos") or td
                        eventos2 = _post_process_eventos(refined["eventos"], tv2, td2)
                        sv2, sd2 = _sum(eventos2)
                        mismatch2 = 0.0
                        if tv2 is not None:
                            mismatch2 += abs(tv2 - sv2)
                        if td2 is not None:
                            mismatch2 += abs(td2 - sd2)
                        if mismatch2 + 0.01 < mismatch:
                            eventos, tv, td = eventos2, tv2, td2

            if comp and not competencia_global:
                competencia_global = comp

            results.append({
                "page_index": idx,
                "competencia": comp,
                "nome": nome,
                "cpf": cpf,
                "liquido": liquido,
                "total_vencimentos": tv,
                "total_descontos": td,
                "eventos": eventos,
                "raw_text": txt,
            })

    return results, competencia_global
