from __future__ import annotations

import os
import io
from pathlib import Path
import zipfile
import re
import json

import pandas as pd
import streamlit as st

SALARIO_BASE_CODES = {'8781','8786'}

import pdfplumber

from src.parsing_recibo import parse_recibo_pagamento_pdf
from src.matching import load_salario_real_xlsx, find_colaborador_ref
from src.cargos import infer_familia, nivel_por_salario, cargo_final
from src.export_xlsx import export_xlsx
from src.receipts_pdf import generate_all_receipts

APP_TITLE = "Demonstrativo de Pagamento Contare"
ROOT = Path(__file__).parent
LOGO_PATH = ROOT / "assets" / "logo.png"

st.set_page_config(page_title=APP_TITLE, layout="wide")


def gpt_disambiguate_name(nome_holerite: str, candidatos: list[str], model: str, api_key: str | None) -> str | None:
    """Usa GPT para escolher o melhor nome da planilha para um nome do holerite (quando há ambiguidade)."""
    if not api_key or not candidatos:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        system = (
            "Você é um assistente de conciliação de nomes de funcionários. "
            "Escolha EXATAMENTE UM nome da lista que corresponde ao nome informado. "
            "Se não houver correspondência confiável, responda null."
        )
        user = {
            "nome_holerite": nome_holerite,
            "candidatos_planilha": candidatos
        }
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
        )
        txt = resp.output_text
        data = json.loads(txt) if txt else {}
        # aceita {"escolha": "..."} ou {"match": "..."} ou {"nome": "..."}
        for k in ("escolha", "match", "nome"):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None
    except Exception:
        return None

def parse_money_any(v) -> float:
    """Converte valores em float aceitando: 303.60, '303,60', '1.518,00', None."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        try:
            return float(v)
        except Exception:
            return 0.0
    s = str(v).strip()
    if not s:
        return 0.0
    s = re.sub(r"[^0-9,\.\-]", "", s)
    if not s:
        return 0.0
    # pt-BR -> float
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return 0.0

def find_referencia_codigo(eventos: list[dict], raw_text: str, codigo: str) -> float | None:
    """Obtém a 'referência' (ex.: 30,00; 2,00) de um código (ex.: 8781) para proporcionalidade."""
    # 1) Pela estrutura do GPT
    for e in (eventos or []):
        if str(e.get("codigo") or "").strip() == str(codigo):
            ref = e.get("referencia")
            if ref is not None:
                v = parse_money_any(ref)
                return v if v > 0 else None
    # 2) Fallback por texto bruto: procura linha com o código e pega o primeiro número pt-BR
    if raw_text:
        for ln in raw_text.splitlines():
            if re.search(rf"\b{re.escape(str(codigo))}\b", ln):
                m2 = re.search(r"(\d{1,3}(?:\.\d{3})*,\d{2})", ln)
                if m2:
                    v = parse_money_any(m2.group(1))
                    return v if v > 0 else None
    return None


@st.cache_data(show_spinner=False)
def render_pdf_page_image(pdf_bytes: bytes, page_index: int, dpi: int = 170):
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[int(page_index)]
        return page.to_image(resolution=dpi).original

# Header
c1, c2 = st.columns([1, 4])
with c1:
    if LOGO_PATH.exists():
        st.image(str(LOGO_PATH), width=160)
with c2:
    st.title(APP_TITLE)
    st.caption("Base: Holerite/Recibo (PDF) + Planilha de salário real (bruto referencial)")

# Sidebar
st.sidebar.header("Configurações")
empresa_nome = st.sidebar.text_input("Empresa", value="Contare")
usar_gpt = st.sidebar.toggle("Usar GPT como extrator principal", value=True)
openai_model = st.sidebar.text_input("Modelo OpenAI", value=os.getenv("OPENAI_MODEL", "gpt-4.1"))
limiar_liquido_zero = st.sidebar.number_input("Limiar de líquido ~0", value=0.0, min_value=0.0)

st.subheader("1) Uploads")
pdf_file = st.file_uploader("Holerite/Recibo (PDF)", type=["pdf"])
xlsx_file = st.file_uploader("Planilha de salário real (XLSX)", type=["xlsx"])

if not pdf_file or not xlsx_file:
    st.info("Envie o PDF e o XLSX para continuar.")
    st.stop()

tmp = ROOT / ".tmp"
tmp.mkdir(exist_ok=True)
pdf_path = tmp / "holerite.pdf"
xlsx_path = tmp / "salarios.xlsx"
pdf_path.write_bytes(pdf_file.getbuffer())
xlsx_path.write_bytes(xlsx_file.getbuffer())

st.subheader("2) Processamento")
if st.button("Processar", type="primary"):
    with st.spinner("Lendo PDF..."):
        colabs, competencia_global = parse_recibo_pagamento_pdf(
            str(pdf_path),
            use_gpt=usar_gpt,
            openai_model=openai_model
        )
    with st.spinner("Lendo XLSX..."):
        df_sal = load_salario_real_xlsx(str(xlsx_path))

    rows = []
    for c in colabs:
        ref = find_colaborador_ref(
            df_sal,
            nome=c.get('nome'),
            gpt_match_fn=lambda nome, cands: gpt_disambiguate_name(nome, cands, model=openai_model, api_key=openai_key),
        )
        nome = c.get("nome") or ref.get("nome")
        cpf = c.get("cpf")
        bruto_ref = ref.get("bruto_referencial")
        status = (ref.get("status") or "").upper()
        depto = ref.get("departamento") or ""
        cargo_base = ref.get("cargo") or ""

        familia = infer_familia(f"{depto} {cargo_base}")
        nivel = nivel_por_salario(bruto_ref) if bruto_ref is not None else None
        cargo_plano = cargo_final(familia, nivel) if bruto_ref is not None else None

        liquido = c.get("liquido")
        eventos = c.get("eventos") or []
        # apurar verbas (a partir do espelho CLT)
        salario_base_clt = 0.0  # 8781 ou 8786 (apenas para referência / base dias)
        ref_base_dias = None
        inss = 0.0
        verba_981 = 0.0

        for e in eventos:
            cod = str(e.get("codigo") or "").strip()
            desc = str(e.get("descricao") or "").upper()
            provento = float(e.get("provento") or 0.0)
            desconto = float(e.get("desconto") or 0.0)
            referencia = str(e.get("referencia") or "").strip()

            if cod in ("8781", "8786"):
                salario_base_clt += provento
                # referência em dias (ex.: 30,00 / 15,00)
                if ref_base_dias is None and referencia:
                    try:
                        ref_base_dias = float(referencia.replace(".", "").replace(",", "."))
                    except Exception:
                        ref_base_dias = None

            if cod == "981":
                verba_981 += desconto

            # INSS deve ser pelo código 998 (e 821 quando existir como INSS diferença férias)
            if cod in ("998", "821"):
                inss += desconto

        # fallback: se não achou referência base por 8781/8786, assume 30
        if ref_base_dias is None:
            ref_base_dias = 30.0
# Cálculo (claro e objetivo)
# Remuneração Bruta proporcional (planilha) = bruto_planilha * (ref_base_dias/30)
# (+) Outros Proventos (tudo que é PROVENTO no holerite, exceto códigos de salário-base 8781/8786)
# (-) Desc. Adiantamento (981)
# (-) Desc. INSS (998/821)
# (-) Outros Descontos (todos os demais DESCONTOS, exceto INSS e 981)
# = Remuneração Líquida a Pagar
regra = "DEMONSTRATIVO: Bruto(planilha proporcional) + Outros Proventos - 981 - INSS - Outros Descontos"
valor_a_pagar = None

# componentes para exibição/relatório (sempre definidos)
outros_proventos = 0.0
desc_adiantamento = float(verba_981 or 0.0)
desc_inss = float(inss or 0.0)
outros_descontos = 0.0

ref_8781 = float(ref_base_dias or 30.0)
bruto_proporcional = None

if bruto_ref is not None:
    bruto_proporcional = float(bruto_ref) * (float(ref_8781) / 30.0)

    for e in eventos:
        cod = str(e.get("codigo") or "").strip()
        pv = float(e.get("provento") or 0.0)
        dc = float(e.get("desconto") or 0.0)

        # Outros proventos: exclui salário-base CLT (substituído pelo bruto da planilha)
        if pv and cod not in ("8781", "8786"):
            outros_proventos += pv

        # Outros descontos: exclui INSS e 981 (já separados)
        if dc and cod not in ("981", "998", "821"):
            outros_descontos += dc

    valor_a_pagar = bruto_proporcional + outros_proventos - desc_adiantamento - desc_inss - outros_descontos
    # nunca negativo
    if valor_a_pagar < 0:
        valor_a_pagar = 0.0


        rows.append({
            "competencia": c.get("competencia") or competencia_global,
            "nome": nome,
            "cpf": cpf,
            "departamento": depto or None,
            "cargo_plano": cargo_plano,
            "status": ref.get("status"),
            "bruto_planilha": bruto_ref,
            "liquido_holerite": liquido,
            "8781_salario_contratual": salario_base_clt,
            "998_inss": inss,
            "981_desc_adiantamento": verba_981,
            "regra_aplicada": regra,
            "valor_a_pagar": valor_a_pagar,
            "remuneracao_bruta_planilha": bruto_ref,
            "referencia_8781": ref_8781,
            "remuneracao_bruta_proporcional": bruto_proporcional,
            "outros_proventos": outros_proventos,
            "desc_adiantamento_981": desc_adiantamento,
            "desc_inss_998": desc_inss,
            "outros_descontos": outros_descontos,
            "page_index": c.get("page_index", 0),
            "eventos": eventos,
            "raw_text": c.get("raw_text", ""),
        })

    df = pd.DataFrame(rows)
    st.session_state["df"] = df
    st.session_state["pdf_bytes"] = pdf_path.read_bytes()
    st.success(f"Processado: {len(df)} colaborador(es).")

df = st.session_state.get("df")
if df is not None:
    tab1, tab2, tab3 = st.tabs(["Consolidado", "Espelho do Recibo", "Exportações"])

    with tab1:
        st.dataframe(df.drop(columns=["eventos","raw_text"], errors="ignore"), use_container_width=True)

    with tab2:
        idx = st.selectbox("Selecione o colaborador", df.index, format_func=lambda i: f"{df.loc[i,'nome']}" + (f" — {df.loc[i,'cpf']}" if str(df.loc[i,'cpf'] or '').strip() else ''))
        row = df.loc[idx]

        cA, cB, cC, cD = st.columns(4)
        cA.metric("Competência", row.get("competencia"))
        cB.metric("Líquido", row.get("liquido_holerite"))
        cC.metric("Bruto (planilha)", row.get("bruto_planilha"))
        st.caption(f"Referência 8781: {row.get('referencia_8781') or '—'} / 30 | Bruto proporcional: {row.get('remuneracao_bruta_proporcional') or '—'}")
        cD.metric("Remuneração Líquida a Pagar", row.get("valor_a_pagar"))

        st.markdown("### Holerite original (imagem)")
        img = render_pdf_page_image(st.session_state["pdf_bytes"], int(row.get("page_index") or 0))
        st.image(img, use_container_width=True)

        st.markdown("### Eventos (extraídos pelo GPT)")
        ev = row.get("eventos") or []
        st.dataframe(pd.DataFrame(ev), use_container_width=True, hide_index=True)

        with st.expander("Texto bruto (debug)"):
            st.text(row.get("raw_text") or "")

    with tab3:
        out_dir = ROOT / ".out"
        out_dir.mkdir(exist_ok=True)

        colA, colB = st.columns(2)
        with colA:
            if st.button("Gerar Excel (Consolidado)"):
                xlsx_out = out_dir / "consolidado.xlsx"
                export_xlsx(df.drop(columns=["eventos","raw_text"], errors="ignore"), str(xlsx_out), logo_path=str(LOGO_PATH) if LOGO_PATH.exists() else None)
                st.download_button("Baixar Consolidado.xlsx", xlsx_out.read_bytes(), file_name="consolidado.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        with colB:
            if st.button("Gerar ZIP de Recibos Complementares (PDF)"):
                pdfs_dir = out_dir / "recibos"
                pdfs_dir.mkdir(exist_ok=True)
                pdfs = generate_all_receipts(df.to_dict(orient="records"), out_dir=str(pdfs_dir), empresa_nome=empresa_nome,
                                            logo_path=str(LOGO_PATH) if LOGO_PATH.exists() else None,
                                            holerite_pdf_bytes=st.session_state.get('pdf_bytes'))
                zip_out = out_dir / "recibos.zip"
                with zipfile.ZipFile(zip_out, "w", zipfile.ZIP_DEFLATED) as z:
                    for p in pdfs:
                        z.write(p, arcname=Path(p).name)
                st.download_button("Baixar Recibos.zip", zip_out.read_bytes(), file_name="recibos.zip", mime="application/zip")
