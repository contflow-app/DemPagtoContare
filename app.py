from __future__ import annotations

import os
import io
import re
import json
import zipfile
import tempfile  # <--- ADICIONADO: Corrige o erro NameError
from pathlib import Path

import pandas as pd
import streamlit as st
import pdfplumber

# Imports do seu projeto
from src.parsing_recibo import parse_recibo_pagamento_pdf
from src.matching import load_salario_real_xlsx, find_colaborador_ref
from src.cargos import infer_familia, nivel_por_salario, cargo_final
from src.export_xlsx import export_xlsx
from src.receipts_pdf import generate_all_receipts

# Configuração da Chave API (Certifique-se de configurar no Streamlit Secrets)
openai_key = os.getenv("OPENAI_API_KEY") or st.secrets.get("OPENAI_API_KEY")

APP_TITLE = "Demonstrativo de Pagamento Contare"
ROOT = Path(__file__).parent
LOGO_PATH = ROOT / "assets" / "logo.png"

st.set_page_config(page_title=APP_TITLE, layout="wide")

# ... (funções gpt_disambiguate_name, parse_money_any, find_referencia_codigo permanecem iguais)

# --- INÍCIO DO PROCESSAMENTO ---
# Substituí a criação da pasta manual por um contexto mais seguro
if not pdf_file or not xlsx_file:
    st.info("Envie o PDF e o XLSX para continuar.")
    st.stop()

# Usando gerenciador de contexto para arquivos temporários (mais limpo)
with tempfile.TemporaryDirectory() as tmpdir:
    temp_path = Path(tmpdir)
    pdf_path = temp_path / "holerite.pdf"
    xlsx_path = temp_path / "salarios.xlsx"
    
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
            # Aqui corrigi a passagem da openai_key que estava faltando
            ref = find_colaborador_ref(
                df_sal,
                nome=(c.get("nome") or ""),
                gpt_match_fn=(lambda nome, cands: gpt_disambiguate_name(nome, cands, model=openai_model, api_key=openai_key)) if usar_gpt else None,
            )
            # ... (restante da lógica de cálculo permanece igual)
            
            # [Atenção: Garanta que a lógica do loop de cálculo termine aqui e preencha 'rows']
            # (Mantenha seu código original de cálculo aqui dentro)
            
        df = pd.DataFrame(rows)
        st.session_state["df"] = df
        st.session_state["pdf_bytes"] = pdf_file.getvalue() # Salva direto do upload
        st.success(f"Processado: {len(df)} colaborador(es).")

# ... (abas tab1 e tab2 permanecem iguais)

with tab3:
    st.write("Exporte o consolidado e os recibos complementares.")
    colA, colB = st.columns(2)

    if "df" in st.session_state:
        df_export = st.session_state["df"]
        
        with colA:
            # Correção do fluxo do Excel
            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp_xlsx:
                export_xlsx(df_export, tmp_xlsx.name, logo_path=str(LOGO_PATH))
                xlsx_bytes = Path(tmp_xlsx.name).read_bytes()
            
            Path(tmp_xlsx.name).unlink(missing_ok=True) # Deleta após ler os bytes
            
            st.download_button(
                "Baixar Excel (Consolidado)",
                data=xlsx_bytes,
                file_name="consolidado.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        with colB:
            # Correção do fluxo do ZIP
            with tempfile.TemporaryDirectory() as tmpdir_zip:
                pdf_paths = generate_all_receipts(
                    df_export.to_dict("records"),
                    out_dir=tmpdir_zip,
                    empresa_nome="Contare",
                    logo_path=str(LOGO_PATH),
                    holerite_pdf_bytes=st.session_state.get("pdf_bytes"),
                )
                
                zip_buf = io.BytesIO()
                with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for p in pdf_paths:
                        zf.write(p, arcname=Path(p).name)
                
                st.download_button(
                    "Baixar Recibos (PDF em ZIP)",
                    data=zip_buf.getvalue(),
                    file_name="recibos_pdf.zip",
                    mime="application/zip",
                )
