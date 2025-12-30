from __future__ import annotations

import os
import io
from pathlib import Path
import zipfile
import pandas as pd
import streamlit as st
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

@st.cache_data(show_spinner=False)
def render_pdf_page_image(pdf_bytes: bytes, page_index: int, dpi: int = 170):
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[int(page_index)]
        return page.to_image(resolution=dpi).original

# Header
c1, c2 = st.columns([1, 4])
with c1:
    if LOGO_PATH.exists():
        st.image(str(LOGO_PATH), width=170)
with c2:
    st.title(APP_TITLE)
    st.caption("Base: Holerite/Recibo (PDF) + Planilha de salário real")

st.sidebar.header("Configurações")
empresa_nome = st.sidebar.text_input("Empresa", value="Contare")
limiar_liquido_zero = st.sidebar.number_input("Limiar líquido ~0", value=0.0, min_value=0.0)
usar_gpt = st.sidebar.toggle("Usar GPT (principal)", value=True)
openai_model = st.sidebar.text_input("Modelo OpenAI", value=os.getenv("OPENAI_MODEL", "gpt-4.1"))

st.subheader("1) Uploads")
pdf_file = st.file_uploader("Holerite / Recibo (PDF)", type=["pdf"])
xlsx_file = st.file_uploader("Planilha de salário real", type=["xlsx"])

if not pdf_file or not xlsx_file:
    st.info("Envie os dois arquivos para continuar.")
    st.stop()

workdir = ROOT / ".tmp_streamlit"
workdir.mkdir(exist_ok=True)

pdf_path = workdir / "holerite.pdf"
xlsx_path = workdir / "salarios.xlsx"
pdf_path.write_bytes(pdf_file.getbuffer())
xlsx_path.write_bytes(xlsx_file.getbuffer())

st.subheader("2) Processamento")

if st.button("Processar", type="primary"):
    colabs, comp_global = parse_recibo_pagamento_pdf(
        str(pdf_path),
        use_gpt=usar_gpt,
        openai_model=openai_model
    )

    df_sal = load_salario_real_xlsx(str(xlsx_path))

    rows = []
    for c in colabs:
        ref = find_colaborador_ref(df_sal, cpf=c.get("cpf"), nome=c.get("nome"))

        nome = c.get("nome") or ref.get("nome")
        cpf = c.get("cpf") or ref.get("cpf")
        bruto_ref = ref.get("bruto_referencial")
        status = (ref.get("status") or "").upper()
        depto = ref.get("departamento")
        cargo = ref.get("cargo")

        familia = infer_familia((depto or "") + " " + (cargo or ""))
        nivel = nivel_por_salario(bruto_ref) if bruto_ref is not None else None
        cargo_plano = cargo_final(familia, nivel)

        liquido = c.get("liquido")
        eventos = c.get("eventos") or []

        # Extrair valores das verbas
        v8781 = 0.0
        v981 = 0.0
        inss = 0.0

        for e in eventos:
            cod = str(e.get("codigo"))
            desc = str(e.get("descricao", "")).upper()
            venc = float(e.get("vencimentos") or 0.0)
            descv = float(e.get("descontos") or 0.0)

            if cod == "8781":
                v8781 += venc
            if cod == "981":
                v981 += descv
            if cod == "998" or "INSS" in desc:
                inss += descv

        # Cálculo
        regra = "INDEFINIDA"
        diferenca = None
        valor_a_pagar = None

        if bruto_ref is not None and liquido is not None:
            liq = float(liquido)
            gatilho_especial = (liq <= float(limiar_liquido_zero)) or (v981 > 0)
            is_ativo = status.startswith("ATIV")

            if gatilho_especial and is_ativo:
                regra = "ESPECIAL: bruto_planilha - salario_contratual - INSS"
                diferenca = float(bruto_ref) - v8781 - inss
            else:
                regra = "PADRAO: bruto_planilha - liquido_holerite"
                diferenca = float(bruto_ref) - liq

            valor_a_pagar = max(diferenca, 0.0)

        rows.append({
            "competencia": c.get("competencia") or comp_global,
            "nome": nome,
            "cpf": cpf,
            "cargo_plano": cargo_plano,
            "bruto_planilha": bruto_ref,
            "liquido_folha": liquido,
            "salario_contratual_8781": v8781,
            "inss": inss,
            "verba_981": v981,
            "regra": regra,
            "valor_a_pagar": valor_a_pagar,
            "page_index": c.get("page_index"),
            "eventos": eventos,
        })

    df = pd.DataFrame(rows)
    st.session_state["df"] = df
    st.session_state["pdf_bytes"] = pdf_path.read_bytes()

    st.success(f"Processado: {len(df)} colaborador(es)")

df = st.session_state.get("df")
if df is not None:
    tab1, tab2 = st.tabs(["Consolidado", "Espelho do Recibo"])

    with tab1:
        st.dataframe(df.drop(columns=["eventos"]), use_container_width=True)

    with tab2:
        idx = st.selectbox("Colaborador", df.index,
                           format_func=lambda i: f"{df.loc[i,'nome']} — {df.loc[i,'cpf']}")
        row = df.loc[idx]

        cA, cB, cC, cD = st.columns(4)
        cA.metric("Competência", row["competencia"])
        cB.metric("Líquido", row["liquido_folha"])
        cC.metric("Bruto", row["bruto_planilha"])
        cD.metric("Valor a pagar", row["valor_a_pagar"])

        st.markdown("### Holerite original")
        img = render_pdf_page_image(st.session_state["pdf_bytes"], row["page_index"])
        st.image(img, use_container_width=True)
