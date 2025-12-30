from __future__ import annotations

import os
import io
from pathlib import Path

import pandas as pd
import streamlit as st
import pdfplumber

from src.parsing_recibo import parse_recibo_pagamento_pdf
from src.matching import load_salario_real_xlsx, find_colaborador_ref
from src.cargos import infer_familia, nivel_por_salario, cargo_final
from src.export_xlsx import export_xlsx
from src.receipts_pdf import generate_all_receipts


# ======================
# CONFIGURAÇÃO BÁSICA
# ======================
APP_TITLE = "Demonstrativo de Pagamento Contare"
ROOT = Path(__file__).parent
LOGO_PATH = ROOT / "assets" / "logo.png"

st.set_page_config(page_title=APP_TITLE, layout="wide")


# ======================
# FUNÇÕES AUXILIARES
# ======================
@st.cache_data(show_spinner=False)
def render_pdf_page_image(pdf_bytes: bytes, page_index: int, dpi: int = 170):
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[int(page_index)]
        return page.to_image(resolution=dpi).original


# ======================
# CABEÇALHO
# ======================
c1, c2 = st.columns([1, 4])
with c1:
    if LOGO_PATH.exists():
        st.image(str(LOGO_PATH), width=160)

with c2:
    st.title(APP_TITLE)
    st.caption("Base: Holerite (PDF) + Planilha de Salário Real")


# ======================
# SIDEBAR
# ======================
st.sidebar.header("Configurações")

limiar_liquido_zero = st.sidebar.number_input(
    "Limiar para líquido zerado",
    value=0.0,
    min_value=0.0
)

usar_gpt = st.sidebar.toggle(
    "Usar GPT como extrator principal",
    value=True
)

openai_model = st.sidebar.text_input(
    "Modelo OpenAI",
    value=os.getenv("OPENAI_MODEL", "gpt-4.1")
)


# ======================
# UPLOADS
# ======================
st.subheader("1) Upload de arquivos")

pdf_file = st.file_uploader("Holerite / Recibo (PDF)", type=["pdf"])
xlsx_file = st.file_uploader("Planilha de salário real", type=["xlsx"])

if not pdf_file or not xlsx_file:
    st.info("Envie o PDF da folha e a planilha de salários para continuar.")
    st.stop()


# ======================
# SALVA TEMPORÁRIOS
# ======================
tmp_dir = ROOT / ".tmp"
tmp_dir.mkdir(exist_ok=True)

pdf_path = tmp_dir / "holerite.pdf"
xlsx_path = tmp_dir / "salarios.xlsx"

pdf_path.write_bytes(pdf_file.getbuffer())
xlsx_path.write_bytes(xlsx_file.getbuffer())


# ======================
# PROCESSAMENTO
# ======================
st.subheader("2) Processamento")

if st.button("Processar", type="primary"):

    colabs, competencia_global = parse_recibo_pagamento_pdf(
        str(pdf_path),
        use_gpt=usar_gpt,
        openai_model=openai_model
    )

    df_salarios = load_salario_real_xlsx(str(xlsx_path))

    linhas = []

    for c in colabs:
        ref = find_colaborador_ref(
            df_salarios,
            cpf=c.get("cpf"),
            nome=c.get("nome")
        )

        nome = c.get("nome") or ref.get("nome")
        cpf = c.get("cpf") or ref.get("cpf")
        bruto_planilha = ref.get("bruto_referencial")
        status = (ref.get("status") or "").upper()
        departamento = ref.get("departamento")
        cargo_base = ref.get("cargo")

        familia = infer_familia(f"{departamento or ''} {cargo_base or ''}")
        nivel = nivel_por_salario(bruto_planilha) if bruto_planilha else None
        cargo_plano = cargo_final(familia, nivel)

        liquido = c.get("liquido")
        eventos = c.get("eventos") or []

        # ======================
        # APURAÇÃO DAS VERBAS
        # ======================
        salario_contratual = 0.0   # 8781
        inss = 0.0                 # 998
        verba_981 = 0.0

        for e in eventos:
            cod = str(e.get("codigo", "")).strip()
            desc = str(e.get("descricao", "")).upper()
            provento = float(e.get("provento") or 0.0)
            desconto = float(e.get("desconto") or 0.0)

            if cod == "8781":
                salario_contratual += provento

            if cod == "981":
                verba_981 += desconto

            if cod == "998" or "INSS" in desc:
                inss += desconto

        # ======================
        # CÁLCULO
        # ======================
        regra = "INDEFINIDA"
        valor_a_pagar = None

        if bruto_planilha is not None and liquido is not None:
            liquido_num = float(liquido)

            gatilho_especial = (
                liquido_num <= limiar_liquido_zero
                or verba_981 > 0
            )

            if gatilho_especial and status.startswith("ATIV"):
                regra = "ESPECIAL: bruto - salário contratual - INSS"
                valor_a_pagar = (
                    bruto_planilha
                    - salario_contratual
                    - inss
                )
            else:
                regra = "PADRÃO: bruto - líquido folha"
                valor_a_pagar = bruto_planilha - liquido_num

            valor_a_pagar = max(valor_a_pagar, 0.0)

        linhas.append({
            "competencia": c.get("competencia") or competencia_global,
            "nome": nome,
            "cpf": cpf,
            "cargo": cargo_plano,
            "bruto_planilha": bruto_planilha,
            "liquido_folha": liquido,
            "salario_contratual_8781": salario_contratual,
            "inss": inss,
            "verba_981": verba_981,
            "regra_aplicada": regra,
            "valor_a_pagar": valor_a_pagar,
            "page_index": c.get("page_index"),
            "eventos": eventos,
        })

    df = pd.DataFrame(linhas)

    st.session_state["df"] = df
    st.session_state["pdf_bytes"] = pdf_path.read_bytes()

    st.success(f"Processamento concluído: {len(df)} colaborador(es)")


# ======================
# RESULTADOS
# ======================
df = st.session_state.get("df")

if df is not None:

    aba1, aba2 = st.tabs(["Consolidado", "Espelho do Recibo"])

    with aba1:
        st.dataframe(
            df.drop(columns=["eventos", "page_index"]),
            use_container_width=True
        )

    with aba2:
        idx = st.selectbox(
            "Selecione o colaborador",
            df.index,
            format_func=lambda i: f"{df.loc[i, 'nome']} — {df.loc[i, 'cpf']}"
        )

        row = df.loc[idx]

        cA, cB, cC, cD = st.columns(4)
        cA.metric("Competência", row["competencia"])
        cB.metric("Líquido folha", row["liquido_folha"])
        cC.metric("Bruto planilha", row["bruto_planilha"])
        cD.metric("Valor a pagar", row["valor_a_pagar"])

        st.markdown("### Holerite original")

        img = render_pdf_page_image(
            st.session_state["pdf_bytes"],
            row["page_index"]
        )
        st.image(img, use_container_width=True)
