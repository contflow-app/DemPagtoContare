from __future__ import annotations

import os
import io
import re
import json
import zipfile
import tempfile  # Garante o import para evitar NameError
from pathlib import Path

import pandas as pd
import streamlit as st
import pdfplumber

from src.parsing_recibo import parse_recibo_pagamento_pdf
from src.matching import load_salario_real_xlsx, find_colaborador_ref
from src.cargos import infer_familia, nivel_por_salario, cargo_final
from src.export_xlsx import export_xlsx
from src.receipts_pdf import generate_all_receipts

# Configurações Iniciais
APP_TITLE = "Demonstrativo de Pagamento Contare"
ROOT = Path(__file__).parent
LOGO_PATH = ROOT / "assets" / "logo.png"
# Chave da API (necessária para o GPT funcionar)
openai_key = os.getenv("OPENAI_API_KEY") or st.secrets.get("OPENAI_API_KEY", None)

st.set_page_config(page_title=APP_TITLE, layout="wide")

# --- FUNÇÕES AUXILIARES (Mantidas) ---
def gpt_disambiguate_name(nome_holerite, candidatos, model, api_key):
    # ... (sua implementação original)
    return None # Simplified for brevity

def parse_money_any(v):
    if v is None: return 0.0
    if isinstance(v, (int, float)): return float(v)
    s = re.sub(r"[^0-9,\.\-]", "", str(v).strip())
    if "," in s: s = s.replace(".", "").replace(",", ".")
    try: return float(s)
    except: return 0.0

@st.cache_data(show_spinner=False)
def render_pdf_page_image(pdf_bytes, page_index):
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[int(page_index)]
        return page.to_image(resolution=170).original

# --- INTERFACE ---
c1, c2 = st.columns([1, 4])
with c1:
    if LOGO_PATH.exists(): st.image(str(LOGO_PATH), width=160)
with c2:
    st.title(APP_TITLE)

# Sidebar
st.sidebar.header("Configurações")
empresa_nome = st.sidebar.text_input("Empresa", value="Contare")
usar_gpt = st.sidebar.toggle("Usar GPT", value=True)
openai_model = st.sidebar.text_input("Modelo OpenAI", value="gpt-4o")

st.subheader("1) Uploads")
pdf_file = st.file_uploader("Holerite (PDF)", type=["pdf"])
xlsx_file = st.file_uploader("Planilha Salários (XLSX)", type=["xlsx"])

# Verificação de Uploads
if not pdf_file or not xlsx_file:
    st.info("Aguardando upload dos arquivos para liberar o processamento.")
    st.stop()

# --- PROCESSAMENTO ---
st.subheader("2) Processamento")
if st.button("Processar Dados", type="primary"):
    with st.spinner("Extraindo informações..."):
        # Usamos caminhos temporários seguros
        with tempfile.TemporaryDirectory() as tmpdir:
            p_path = Path(tmpdir) / "h.pdf"
            x_path = Path(tmpdir) / "s.xlsx"
            p_path.write_bytes(pdf_file.getvalue())
            x_path.write_bytes(xlsx_file.getvalue())

            colabs, comp_global = parse_recibo_pagamento_pdf(str(p_path), use_gpt=usar_gpt, openai_model=openai_model)
            df_sal = load_salario_real_xlsx(str(x_path))

            rows = []
            for c in colabs:
                # Lógica de Matching
                ref = find_colaborador_ref(
                    df_sal, 
                    nome=c.get("nome", ""), 
                    gpt_match_fn=(lambda n, cds: gpt_disambiguate_name(n, cds, openai_model, openai_key)) if usar_gpt else None
                )
                
                # --- Seus Cálculos (Simplificados para o exemplo, mantenha sua lógica interna) ---
                ref_base_dias = 30.0 # Exemplo: extrair do loop de eventos como você já faz
                # ... (insira aqui sua lógica de cálculo de valor_a_pagar) ...
                
                rows.append({
                    "nome": c.get("nome") or ref.get("nome"),
                    "valor_a_pagar": 1000.0, # Exemplo
                    "eventos": c.get
