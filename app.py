from __future__ import annotations

import os
from pathlib import Path
import zipfile

import pandas as pd
import streamlit as st

from src.parsing_folha_extrato import parse_extrato_mensal_pdf
from src.matching import load_salario_real_xlsx, find_colaborador_ref
from src.cargos import infer_familia, nivel_por_salario, cargo_final
from src.export_xlsx import export_xlsx
from src.receipts_pdf import generate_all_receipts


APP_TITLE = "Demonstrativo de Pagamento Contare"
ROOT = Path(__file__).parent
LOGO_PATH = ROOT / "assets" / "logo.png"

st.set_page_config(page_title=APP_TITLE, layout="wide")

c1, c2 = st.columns([1, 4])
with c1:
    if LOGO_PATH.exists():
        st.image(str(LOGO_PATH), width=170)
with c2:
    st.title(APP_TITLE)
    st.caption(
        "Base: **Extrato Mensal da Folha (PDF)**.\n\n"
        "Regra padrão: **Complemento = Bruto(planilha) − Líquido(folha)**.\n"
        "Regra especial (líquido ~0 & ATIVO): **Complemento = Bruto(planilha) − verba 8781 − verba 981**."
    )

st.divider()

st.sidebar.header("Configurações")
empresa_nome = st.sidebar.text_input("Empresa (no recibo)", value="Contare")
limiar_liquido_zero = st.sidebar.number_input(
    "Limiar p/ considerar líquido como 'zerado' (R$)",
    value=0.0, min_value=0.0, step=1.0
)
gerar_recibos_para_zero = st.sidebar.toggle("Gerar recibo mesmo se Valor a pagar = 0", value=False)

usar_gpt = st.sidebar.toggle("Usar GPT (fallback) para extrair Nome/CPF quando falhar", value=False)
openai_model = st.sidebar.text_input("Modelo OpenAI", value=os.getenv("OPENAI_MODEL", "gpt-4.1"))
st.sidebar.caption("Se ativar GPT: defina OPENAI_API_KEY (Streamlit Cloud > Secrets).")

st.subheader("1) Uploads")
pdf_file = st.file_uploader("Extrato Mensal da Folha (PDF)", type=["pdf"])
xlsx_file = st.file_uploader("Planilha de salário real (Bruto referencial)", type=["xlsx"])

if not pdf_file or not xlsx_file:
    st.info("Envie o PDF do Extrato Mensal e a planilha Excel para continuar.")
    st.stop()

workdir = Path(st.session_state.get("workdir", ROOT / ".tmp_streamlit"))
workdir.mkdir(parents=True, exist_ok=True)
st.session_state["workdir"] = str(workdir)

pdf_path = workdir / "extrato_mensal.pdf"
xlsx_path = workdir / "salarios.xlsx"
pdf_path.write_bytes(pdf_file.getbuffer())
xlsx_path.write_bytes(xlsx_file.getbuffer())

st.subheader("2) Processamento")

if st.button("Processar", type="primary"):
    with st.spinner("Lendo Extrato Mensal (PDF)..."):
        colabs, competencia_global = parse_extrato_mensal_pdf(
            str(pdf_path),
            use_gpt_fallback=usar_gpt,
            openai_model=openai_model
        )

    with st.spinner("Lendo planilha de salários..."):
        df_sal = load_salario_real_xlsx(str(xlsx_path))

    rows = []
    for c in colabs:
        ref = find_colaborador_ref(df_sal, cpf=c.get("cpf"), nome=c.get("nome"))

        # Prioriza dados da planilha quando o PDF falhar (principalmente Nome)
        nome_final = c.get("nome") or ref.get("nome")
        cpf_final = c.get("cpf") or ref.get("cpf")

        bruto_ref = ref.get("bruto_referencial")   # BRUTO REFERENCIAL (PLANILHA)
        status_colab = (ref.get("status") or "").strip()
        depto_ref = ref.get("departamento") or c.get("departamento")
        cargo_ref = ref.get("cargo") or c.get("cargo")

        familia = infer_familia((depto_ref or "") + " " + (cargo_ref or ""))
        nivel = nivel_por_salario(bruto_ref) if bruto_ref is not None else None
        cargo_plano = cargo_final(familia, nivel)

        liquido = c.get("liquido")                 # LÍQUIDO (PDF)
        v8781 = c.get("verba_8781")                # 8781 (PDF)
        v981 = c.get("verba_981")                  # 981 (PDF)

        regra = "PADRAO: bruto_ref - liquido_folha"
        diferenca = None
        valor_a_pagar = None
        notas = []

        if nome_final is None:
            notas.append("nome não identificado (PDF e planilha)")
        if cpf_final is None:
            notas.append("cpf não identificado (PDF e planilha)")
        if bruto_ref is None:
            notas.append("bruto referencial não encontrado na planilha (match falhou)")
        if liquido is None:
            notas.append("líquido não encontrado no PDF")

        # ✅ REGRA DO LÍQUIDO ZERADO (corrigida): só aplica se ATIVO e tiver 8781 e 981
        if bruto_ref is not None and liquido is not None:
            liquido_num = float(liquido)
            limiar = float(limiar_liquido_zero)

            aplica_especial = (
                liquido_num <= limiar and
                status_colab.upper().startswith("ATIV") and
                (v8781 is not None) and
                (v981 is not None)
            )

            if aplica_especial:
                regra = "ESPECIAL (liq~0 & ATIVO): bruto_ref - 8781 - 981"
                diferenca = float(bruto_ref) - float(v8781) - float(v981)
            else:
                if liquido_num <= limiar and status_colab.upper().startswith("ATIV"):
                    notas.append("líquido ~0, mas sem 8781/981 no PDF -> usando regra padrão")
                diferenca = float(bruto_ref) - liquido_num

            valor_a_pagar = max(diferenca, 0.0)

        status_conf = "OK"
        if (nome_final is None) or (cpf_final is None) or (bruto_ref is None) or (liquido is None) or (valor_a_pagar is None):
            status_conf = "REVISAR"

        rows.append({
            "competencia": c.get("competencia") or competencia_global,
            "nome": nome_final,
            "cpf": cpf_final,
            "departamento": depto_ref or c.get("departamento"),
            "cargo_folha": c.get("cargo"),
            "cargo_plano": cargo_plano,
            "status": status_colab or None,
            "status_conferencia": status_conf,

            "bruto_referencial_planilha": bruto_ref,
            "liquido_folha": liquido,

            "verba_8781_salario_contratual": v8781,
            "verba_981_desc_adiantamento": v981,

            "regra_aplicada": regra,
            "diferenca_calculada": diferenca,
            "valor_a_pagar": valor_a_pagar,

            "notas": "; ".join(notas),

            "eventos_folha": c.get("eventos") or [],
            "raw_block": c.get("raw_block") or "",
        })

    df = pd.DataFrame(rows)
    st.session_state["df"] = df
    st.session_state["competencia_global"] = competencia_global
    st.success(f"Processado: {len(df)} colaborador(es).")

df = st.session_state.get("df")
if df is not None:
    tab1, tab2, tab3 = st.tabs(["Consolidado", "Espelho (Extrato Mensal)", "Exportações"])

    with tab1:
        st.subheader("Prévia do consolidado")
        st.dataframe(df.drop(columns=["eventos_folha", "raw_block"], errors="ignore"),
                     use_container_width=True, hide_index=True)

    with tab2:
        st.subheader("Espelho do Extrato Mensal (por colaborador)")
        nomes = df["nome"].fillna("(Sem nome)").astype(str).tolist()
        idx = st.selectbox("Selecione o colaborador", list(range(len(nomes))),
                           format_func=lambda i: f"{nomes[i]} — {df.iloc[i].get('cpf') or '-'}")
        row = df.iloc[int(idx)].to_dict()

        cA, cB, cC, cD = st.columns(4)
        with cA:
            st.metric("Competência", row.get("competencia") or "-")
        with cB:
            st.metric("Líquido (folha)", f"{row.get('liquido_folha'):.2f}" if row.get("liquido_folha") is not None else "-")
        with cC:
            st.metric("Bruto (planilha)", f"{row.get('bruto_referencial_planilha'):.2f}" if row.get("bruto_referencial_planilha") is not None else "-")
        with cD:
            st.metric("Valor a pagar", f"{row.get('valor_a_pagar'):.2f}" if row.get("valor_a_pagar") is not None else "-")

        st.markdown("### Eventos (espelho) extraídos do PDF")
        ev = row.get("eventos_folha") or []
        if ev:
            st.dataframe(pd.DataFrame(ev), use_container_width=True, hide_index=True)
        else:
            st.info("Sem eventos extraídos (dependendo do layout, pode estar tudo no texto).")

        with st.expander("Texto do bloco (debug)"):
            st.text(row.get("raw_block") or "")

    with tab3:
        st.subheader("Exportações")
        out_xlsx = Path(st.session_state["workdir"]) / "demonstrativo_consolidado.xlsx"
        out_conf = Path(st.session_state["workdir"]) / "relatorio_conferencia.xlsx"
        out_pdf_dir = Path(st.session_state["workdir"]) / "recibos_complementares"
        out_zip = Path(st.session_state["workdir"]) / "recibos_complementares.zip"

        cA, cB, cC = st.columns(3)

        with cA:
            if st.button("Gerar Excel (Consolidado)"):
                export_xlsx(df.drop(columns=["eventos_folha", "raw_block"], errors="ignore"),
                            str(out_xlsx),
                            logo_path=str(LOGO_PATH) if LOGO_PATH.exists() else None)
                st.download_button("Baixar Excel (Consolidado)", out_xlsx.read_bytes(),
                                   file_name=out_xlsx.name,
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        with cB:
            if st.button("Gerar Excel (Conferência)"):
                conf = df[
                    (df["nome"].isna()) |
                    (df["cpf"].isna()) |
                    (df["bruto_referencial_planilha"].isna()) |
                    (df["liquido_folha"].isna()) |
                    (df["valor_a_pagar"].isna()) |
                    (df["status_conferencia"] == "REVISAR")
                ].copy()
                export_xlsx(conf.drop(columns=["eventos_folha", "raw_block"], errors="ignore"),
                            str(out_conf),
                            logo_path=str(LOGO_PATH) if LOGO_PATH.exists() else None)
                st.download_button("Baixar Excel (Conferência)", out_conf.read_bytes(),
                                   file_name=out_conf.name,
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        with cC:
            if st.button("Gerar ZIP de Recibos Complementares (PDF)"):
                out_pdf_dir.mkdir(parents=True, exist_ok=True)
                records = df.to_dict(orient="records")
                if not gerar_recibos_para_zero:
                    records = [r for r in records if (r.get("valor_a_pagar") is not None and float(r.get("valor_a_pagar")) > 0)]
                pdfs = generate_all_receipts(
                    records,
                    out_dir=str(out_pdf_dir),
                    empresa_nome=empresa_nome,
                    logo_path=str(LOGO_PATH) if LOGO_PATH.exists() else None,
                )
                with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
                    for p in pdfs:
                        z.write(p, arcname=Path(p).name)
                st.download_button("Baixar ZIP de PDFs", out_zip.read_bytes(),
                                   file_name=out_zip.name, mime="application/zip")
