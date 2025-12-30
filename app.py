from __future__ import annotations

import os
from pathlib import Path
import zipfile

import pandas as pd
import streamlit as st

from src.parsing_recibo_pagamento import parse_recibo_pagamento_pdf
from src.matching import load_salario_real_xlsx, find_colaborador_ref
from src.cargos import infer_familia, nivel_por_salario, cargo_final
from src.export_xlsx import export_xlsx
from src.receipts_pdf import generate_all_receipts


APP_TITLE = "Demonstrativo de Pagamento Contare"
ROOT = Path(__file__).parent
LOGO_PATH = ROOT / "assets" / "logo.png"


st.set_page_config(page_title=APP_TITLE, layout="wide")

# Header
c1, c2 = st.columns([1, 4])
with c1:
    if LOGO_PATH.exists():
        st.image(str(LOGO_PATH), width=170)
with c2:
    st.title(APP_TITLE)
    st.caption(
        "Gera **Recibo Complementar (Extra-folha)** com **espelho da folha (CLT)** a partir do **Recibo de Pagamento (PDF)**, "
        "cruza com o **Bruto referencial** da planilha Excel e calcula o complemento."
    )

st.divider()

st.sidebar.header("Configurações")
empresa_nome = st.sidebar.text_input("Empresa (no recibo)", value="Contare")
usar_gpt = st.sidebar.toggle("Usar GPT como fallback (extração de eventos difíceis)", value=False)
openai_model = st.sidebar.text_input("Modelo OpenAI", value=os.getenv("OPENAI_MODEL", "gpt-4.1"))
limiar_liquido_zero = st.sidebar.number_input("Limiar para 'líquido ~0' (R$)", value=0.0, min_value=0.0, step=1.0)

st.sidebar.markdown("---")
st.sidebar.caption("Se ativar GPT, defina OPENAI_API_KEY no ambiente (Streamlit Cloud > Secrets).")

st.subheader("1) Uploads")
recibo_pdf = st.file_uploader("Recibo(s) de Pagamento (PDF da folha)", type=["pdf"])
salarios_xlsx = st.file_uploader("Planilha de salário real (bruto referencial)", type=["xlsx"])

if not recibo_pdf or not salarios_xlsx:
    st.info("Envie o PDF do(s) recibo(s) e a planilha Excel para continuar.")
    st.stop()

workdir = Path(st.session_state.get("workdir", ROOT / ".tmp_streamlit"))
workdir.mkdir(parents=True, exist_ok=True)
st.session_state["workdir"] = str(workdir)

pdf_path = workdir / "recibos_folha.pdf"
xlsx_path = workdir / "salarios.xlsx"
pdf_path.write_bytes(recibo_pdf.getbuffer())
xlsx_path.write_bytes(salarios_xlsx.getbuffer())

st.subheader("2) Processamento")

if st.button("Processar", type="primary"):
    with st.spinner("Lendo PDF de Recibos de Pagamento..."):
        recibos = parse_recibo_pagamento_pdf(str(pdf_path), use_gpt_fallback=usar_gpt, openai_model=openai_model)

    with st.spinner("Lendo planilha de salários..."):
        df_sal = load_salario_real_xlsx(str(xlsx_path))

    rows = []
    for r in recibos:
        ref = find_colaborador_ref(df_sal, cpf=r.cpf, nome=r.nome)

        bruto_ref = ref.get("bruto_referencial")
        status_colab = ref.get("status")
        depto_ref = ref.get("departamento") or r.departamento
        cargo_ref = ref.get("cargo") or r.cargo

        # Regra padrão (preferida): complemento = bruto_ref - liquido_folha
        liquido = r.liquido

        # Caso especial (quando líquido ~0): permitir usar 8781 e 981 se existirem
        v8781 = r.evento_valor("8781", tipo="venc")
        v981 = r.evento_valor("981", tipo="desc")

        familia = infer_familia(depto_ref or cargo_ref or "")
        nivel = nivel_por_salario(bruto_ref) if bruto_ref is not None else None
        cargo_plano = cargo_final(familia, nivel)

        regra = "PADRAO: bruto_ref - liquido_folha"
        valor_a_pagar = None
        diferenca = None

        if bruto_ref is not None and liquido is not None:
            if (limiar_liquido_zero is not None) and (liquido <= float(limiar_liquido_zero)) and (status_colab or "").upper().startswith("ATIV") and (v8781 is not None) and (v981 is not None):
                regra = "ESPECIAL (liq~0 & ATIVO): bruto_ref - 8781 - 981"
                diferenca = float(bruto_ref) - float(v8781) - float(v981)
            else:
                diferenca = float(bruto_ref) - float(liquido)

            valor_a_pagar = max(diferenca, 0.0)

        rows.append({
            "competencia": r.competencia,
            "nome": r.nome,
            "cpf": r.cpf,
            "matricula": r.matricula,
            "departamento": depto_ref or r.departamento,
            "cargo_folha": r.cargo,
            "cargo_plano": cargo_plano,
            "status": status_colab,
            "bruto_referencial_planilha": bruto_ref,
            "total_vencimentos_folha": r.total_venc,
            "total_descontos_folha": r.total_desc,
            "liquido_folha": r.liquido,
            "verba_8781_salario_contratual": v8781,
            "verba_981_desc_adiantamento": v981,
            "regra_aplicada": regra,
            "diferenca_calculada": diferenca,
            "valor_a_pagar": valor_a_pagar,
            "page_index": r.page_index,
            "eventos_folha": [e.to_dict() for e in r.eventos],
        })

    df = pd.DataFrame(rows)
    st.session_state["df"] = df
    st.success(f"Processado: {len(df)} recibo(s).")

df = st.session_state.get("df")
if df is not None:
    # Tabs
    tab1, tab2, tab3 = st.tabs(["Consolidado", "Espelho do Recibo Original", "Exportações"])

    with tab1:
        st.subheader("Prévia do consolidado")
        st.dataframe(df.drop(columns=["eventos_folha"], errors="ignore"), use_container_width=True, hide_index=True)

        st.markdown("**Dica:** os eventos detalhados (espelho da folha) estão na aba **Espelho do Recibo Original**.")

    with tab2:
        st.subheader("Espelho do Recibo Original (por colaborador)")
        # Seleção do colaborador
        nomes = df["nome"].fillna("(Sem nome)").astype(str).tolist()
        idx = st.selectbox("Selecione o colaborador", list(range(len(nomes))), format_func=lambda i: f"{nomes[i]} — {df.iloc[i].get('cpf') or '-'} — {df.iloc[i].get('competencia') or '-'}")
        row = df.iloc[int(idx)].to_dict()

        # Mostrar mini-resumo
        cA, cB, cC, cD = st.columns(4)
        with cA:
            st.metric("Competência", row.get("competencia") or "-")
        with cB:
            st.metric("Líquido (folha)", f"{row.get('liquido_folha'):.2f}" if row.get("liquido_folha") is not None else "-")
        with cC:
            st.metric("Bruto (planilha)", f"{row.get('bruto_referencial_planilha'):.2f}" if row.get("bruto_referencial_planilha") is not None else "-")
        with cD:
            st.metric("Valor a pagar", f"{row.get('valor_a_pagar'):.2f}" if row.get("valor_a_pagar") is not None else "-")

        st.markdown("### Tabela de eventos (espelho)")
        eventos = row.get("eventos_folha") or []
        if eventos:
            ev_df = pd.DataFrame(eventos)
            st.dataframe(ev_df, use_container_width=True, hide_index=True)
        else:
            st.warning("Este recibo não trouxe eventos extraídos (tente ativar GPT fallback no sidebar).")

        st.markdown("### Totais")
        st.write({
            "Total Vencimentos": row.get("total_vencimentos_folha"),
            "Total Descontos": row.get("total_descontos_folha"),
            "Valor Líquido": row.get("liquido_folha"),
        })

        st.markdown("### Pré-visualização do PDF original (página)")
        # Renderiza a página como imagem para conferência visual
        try:
            import pdfplumber
            from PIL import Image
            pdf_path = Path(st.session_state["workdir"]) / "recibos_folha.pdf"
            with pdfplumber.open(str(pdf_path)) as pdf:
                pno = int(row.get('page_index') or 0)
                if pno >= len(pdf.pages):
                    pno = 0
                im = pdf.pages[pno].to_image(resolution=150).original
            st.image(im, caption=f"Página {pno+1} do Recibo de Pagamento (original)", use_container_width=True)
        except Exception as e:
            st.info("Não foi possível renderizar a página do PDF aqui. (Isso não afeta a geração dos recibos.)")
            st.code(str(e))

        with st.expander("Texto extraído (debug)"):
            # Mostra texto bruto se existir no DF (não persistimos raw_text no DF por padrão)
            st.info("Para debug avançado, habilite logging no parser.")

    with tab3:
        st.subheader("Exportações")
        out_xlsx = Path(st.session_state["workdir"]) / "demonstrativo_consolidado.xlsx"
        out_conf = Path(st.session_state["workdir"]) / "relatorio_conferencia.xlsx"
        out_pdf_dir = Path(st.session_state["workdir"]) / "recibos_complementares"
        out_zip = Path(st.session_state["workdir"]) / "recibos_complementares.zip"

        cA, cB, cC = st.columns(3)

        with cA:
            if st.button("Gerar Excel (Consolidado)"):
                export_xlsx(df.drop(columns=["eventos_folha"], errors="ignore"), str(out_xlsx), logo_path=str(LOGO_PATH) if LOGO_PATH.exists() else None)
                st.download_button("Baixar Excel (Consolidado)", out_xlsx.read_bytes(), file_name=out_xlsx.name,
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        with cB:
            if st.button("Gerar Excel (Conferência)"):
                conf = df[(df["nome"].isna()) | (df["cpf"].isna()) | (df["bruto_referencial_planilha"].isna()) | (df["liquido_folha"].isna()) | (df["valor_a_pagar"].isna())].copy()
                export_xlsx(conf.drop(columns=["eventos_folha"], errors="ignore"), str(out_conf), logo_path=str(LOGO_PATH) if LOGO_PATH.exists() else None)
                st.download_button("Baixar Excel (Conferência)", out_conf.read_bytes(), file_name=out_conf.name,
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        with cC:
            if st.button("Gerar ZIP de Recibos Complementares (PDF)"):
                out_pdf_dir.mkdir(parents=True, exist_ok=True)
                pdfs = generate_all_receipts(
                    df.to_dict(orient="records"),
                    out_dir=str(out_pdf_dir),
                    empresa_nome=empresa_nome,
                    logo_path=str(LOGO_PATH) if LOGO_PATH.exists() else None,
                )
                with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
                    for p in pdfs:
                        z.write(p, arcname=Path(p).name)
                st.download_button("Baixar ZIP de PDFs", out_zip.read_bytes(), file_name=out_zip.name, mime="application/zip")
