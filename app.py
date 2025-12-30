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
        ref = find_colaborador_ref(df_sal, cpf=c.get("cpf"), nome=c.get("nome"))
        nome = c.get("nome") or ref.get("nome")
        cpf = c.get("cpf") or ref.get("cpf")
        bruto_ref = ref.get("bruto_referencial")
        status = (ref.get("status") or "").upper()
        depto = ref.get("departamento") or ""
        cargo_base = ref.get("cargo") or ""

        familia = infer_familia(f"{depto} {cargo_base}")
        nivel = nivel_por_salario(bruto_ref) if bruto_ref is not None else None
        cargo_plano = cargo_final(familia, nivel)

        liquido = c.get("liquido")
        eventos = c.get("eventos") or []

        # apurar verbas
        salario_contratual_8781 = 0.0
        inss = 0.0
        verba_981 = 0.0
        for e in eventos:
            cod = str(e.get("codigo") or "").strip()
            desc = str(e.get("descricao") or "").upper()
            provento = float(e.get("provento") or 0.0)
            desconto = float(e.get("desconto") or 0.0)
            if cod == "8781":
                salario_contratual_8781 += provento
            if cod == "981":
                verba_981 += desconto
            if cod == "998" or "INSS" in desc:
                inss += desconto

        regra = "INDEFINIDA"
        valor_a_pagar = None
        if bruto_ref is not None and liquido is not None:
            liq = float(liquido)
            gatilho_especial = (liq <= float(limiar_liquido_zero)) or (verba_981 > 0.0)
            is_ativo = status.startswith("ATIV")
            if gatilho_especial and is_ativo:
                regra = "ESPECIAL: bruto_planilha - 8781 - INSS (liq baixo/981 presente & ATIVO)"
                valor_a_pagar = float(bruto_ref) - float(salario_contratual_8781) - float(inss)
            else:
                regra = "PADRÃO: bruto_planilha - líquido_holerite"
                valor_a_pagar = float(bruto_ref) - liq
            valor_a_pagar = max(valor_a_pagar, 0.0)

        rows.append({
            "competencia": c.get("competencia") or competencia_global,
            "nome": nome,
            "cpf": cpf,
            "departamento": depto or None,
            "cargo_plano": cargo_plano,
            "status": ref.get("status"),
            "bruto_planilha": bruto_ref,
            "liquido_holerite": liquido,
            "8781_salario_contratual": salario_contratual_8781,
            "998_inss": inss,
            "981_desc_adiantamento": verba_981,
            "regra_aplicada": regra,
            "valor_a_pagar": valor_a_pagar,
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
        idx = st.selectbox("Selecione o colaborador", df.index, format_func=lambda i: f"{df.loc[i,'nome']} — {df.loc[i,'cpf']}")
        row = df.loc[idx]

        cA, cB, cC, cD = st.columns(4)
        cA.metric("Competência", row.get("competencia"))
        cB.metric("Líquido", row.get("liquido_holerite"))
        cC.metric("Bruto (planilha)", row.get("bruto_planilha"))
        cD.metric("Valor a pagar", row.get("valor_a_pagar"))

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
                                            logo_path=str(LOGO_PATH) if LOGO_PATH.exists() else None)
                zip_out = out_dir / "recibos.zip"
                with zipfile.ZipFile(zip_out, "w", zipfile.ZIP_DEFLATED) as z:
                    for p in pdfs:
                        z.write(p, arcname=Path(p).name)
                st.download_button("Baixar Recibos.zip", zip_out.read_bytes(), file_name="recibos.zip", mime="application/zip")
