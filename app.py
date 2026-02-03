from __future__ import annotations

import os
import io
from pathlib import Path
import zipfile
import re

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
        ref = find_colaborador_ref(df_sal, cpf=c.get("cpf"), nome=c.get("nome"))
        nome = c.get("nome") or ref.get("nome")
        cpf = c.get("cpf") or ref.get("cpf")
        bruto_ref = ref.get("bruto_referencial")
        status = (ref.get("status") or "").upper()
        depto = ref.get("departamento") or ""
        cargo_base = ref.get("cargo") or ""

        familia = infer_familia(f"{depto} {cargo_base}")
        nivel = nivel_por_salario(bruto_ref) if bruto_ref is not None else None
        cargo_plano = cargo_final(familia, nivel) if bruto_ref is not None else None

        liquido = c.get("liquido")
        eventos = c.get("eventos") or []

        # apurar verbas
        salario_contratual_8781 = 0.0
        inss = 0.0
        verba_981 = 0.0
        for e in eventos:
            cod = str(e.get("codigo") or "").strip()
            desc = str(e.get("descricao") or "").upper()
            provento = parse_money_any(e.get('provento')) or parse_money_any(e.get('vencimentos'))
            desconto = parse_money_any(e.get('desconto')) or parse_money_any(e.get('descontos'))
            if cod == "8781":
                salario_contratual_8781 += provento
            if cod == "981":
                verba_981 += desconto
            if cod == "998" or "INSS" in desc:
                inss += desconto



        # Cálculo (V7 - claro e objetivo):
        # Remuneração Bruta (planilha)
        # (+) Outros Proventos (holerite, exceto 8781)
        # (-) Desc. Adiantamento (981)
        # (-) Desc. INSS (998 ou descrição INSS)
        # (-) Outros Descontos (demais descontos)
        # = Remuneração Líquida (VALOR A PAGAR)
        regra = "DEMONSTRATIVO: Bruto(planilha) + Outros Proventos - 981 - INSS - Outros Descontos"
        valor_a_pagar = None

        # componentes para exibição/relatório
        outros_proventos = None
        desc_adiantamento = None
        desc_inss = None
        outros_descontos = None

        # Inicializa para evitar NameError em casos sem bruto_ref
        ref_8781 = None
        bruto_proporcional = None

        if bruto_ref is not None:
            # Recalcular componentes a partir dos eventos (independente do 'liquido' do holerite)
            total_proventos = 0.0
            total_descontos = 0.0

            for e in eventos:
                pro = parse_money_any(e.get('provento')) or parse_money_any(e.get('vencimentos'))
                des = parse_money_any(e.get('desconto')) or parse_money_any(e.get('descontos'))
                total_proventos += float(pro or 0.0)
                total_descontos += float(des or 0.0)

            # Outros proventos = total proventos - 8781 (salário contratual)
            outros_proventos = max(float(total_proventos) - float(salario_contratual_8781), 0.0)

            desc_adiantamento = float(verba_981)
            desc_inss = float(inss)

            # Outros descontos = total descontos - (981 + INSS)
            outros_descontos = max(float(total_descontos) - float(desc_adiantamento) - float(desc_inss), 0.0)

            # Proporcionalidade por referência: 30,00 = salário cheio; diferente disso -> proporcional
            ref_8781 = find_referencia_codigo(eventos, c.get('raw_text',''), '8781')
            bruto_base = float(bruto_ref)
            bruto_proporcional = bruto_base
            if ref_8781 is not None and float(ref_8781) > 0 and abs(float(ref_8781) - 30.0) > 1e-6:
                bruto_proporcional = bruto_base * (float(ref_8781) / 30.0)

            valor_a_pagar = float(bruto_proporcional) + float(outros_proventos) - float(desc_adiantamento) - float(desc_inss) - float(outros_descontos)
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
        idx = st.selectbox("Selecione o colaborador", df.index, format_func=lambda i: f"{df.loc[i,'nome']} — {df.loc[i,'cpf']}")
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
