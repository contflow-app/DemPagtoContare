from __future__ import annotations

import os
import io
import zipfile
import tempfile
import json
from pathlib import Path

import pandas as pd
import streamlit as st
import pdfplumber

from src.parsing_recibo import parse_recibo_pagamento_pdf
from src.matching import load_salario_real_xlsx, find_colaborador_ref
from src.cargos import infer_familia, nivel_por_salario, cargo_final
from src.export_xlsx import export_xlsx
from src.receipts_pdf import generate_all_receipts
from src.utils import parse_money_any

APP_TITLE = "Demonstrativo de Pagamento Contare"
ROOT = Path(__file__).parent
LOGO_PATH = ROOT / "assets" / "logo.png"

st.set_page_config(page_title=APP_TITLE, layout="wide")


def gpt_disambiguate_name(nome_holerite: str, candidatos: list[str], model: str) -> str | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not candidatos:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        system = (
            "Você concilia nomes de funcionários. "
            "Escolha exatamente UM nome da lista que corresponde ao nome informado. "
            "Se não houver correspondência confiável, responda null."
        )
        user = {"nome_holerite": nome_holerite, "candidatos_planilha": candidatos}
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.output_text or "{}")
        for k in ("escolha", "match", "nome"):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None
    except Exception:
        return None


@st.cache_data(show_spinner=False)
def render_pdf_page_image(pdf_bytes: bytes, page_index: int, dpi: int = 170):
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[int(page_index)]
        return page.to_image(resolution=dpi).original


def compute_valor_a_pagar(
    bruto_planilha: float | None,
    referencia_dias: float,
    salario_contratual_holerite: float,
    liquido_holerite: float | None,
    limiar_zero: float = 0.0,
):
    """
    REGRA NOVA:
      bruto_prop = bruto_planilha * (ref/30)

      Se liquido_holerite > limiar_zero:
        valor = bruto_prop - salario_contratual + liquido_holerite
      Senão:
        valor = bruto_prop - salario_contratual
    """
    if bruto_planilha is None:
        return None, "SEM_BRUTO_PLANILHA", None

    bruto_prop = float(bruto_planilha) * (float(referencia_dias) / 30.0)
    liq = float(liquido_holerite or 0.0)

    if liq > float(limiar_zero):
        valor = bruto_prop - float(salario_contratual_holerite or 0.0) + liq
        regra = "PADRÃO: bruto(planilha prop.) - salário (8781/8786) + líquido holerite"
    else:
        valor = bruto_prop - float(salario_contratual_holerite or 0.0)
        regra = "ESPECIAL: líquido=0 -> bruto(planilha prop.) - salário (8781/8786)"

    if valor < 0:
        valor = 0.0

    return valor, regra, bruto_prop


# Header
c1, c2 = st.columns([1, 4])
with c1:
    if LOGO_PATH.exists():
        st.image(str(LOGO_PATH), width=160)
with c2:
    st.title(APP_TITLE)
    st.caption("Base: Holerite/Recibo (PDF) + Planilha de salário real (bruto referencial).")

# Sidebar
st.sidebar.header("Configurações")
empresa_nome = st.sidebar.text_input("Empresa", value="Contare")
usar_gpt = st.sidebar.toggle("Usar GPT na extração (se OPENAI_API_KEY estiver configurada)", value=True)
openai_model = st.sidebar.text_input("Modelo OpenAI", value=os.getenv("OPENAI_MODEL", "gpt-4.1"))
limiar_liquido_zero = st.sidebar.number_input("Limiar de líquido ~0", value=0.0, min_value=0.0)

st.subheader("1) Uploads")
pdf_file = st.file_uploader("Holerite/Recibo (PDF)", type=["pdf"])
xlsx_file = st.file_uploader("Planilha de salário real (XLSX) — colunas: Nome e Bruto", type=["xlsx"])

if not pdf_file or not xlsx_file:
    st.info("Envie o PDF e o XLSX para continuar.")
    st.stop()

tmp_dir = ROOT / ".tmp"
tmp_dir.mkdir(exist_ok=True)
pdf_path = tmp_dir / "holerite.pdf"
xlsx_path = tmp_dir / "salarios.xlsx"
pdf_path.write_bytes(pdf_file.getbuffer())
xlsx_path.write_bytes(xlsx_file.getbuffer())

st.subheader("2) Processamento")
if st.button("Processar", type="primary"):
    with st.spinner("Lendo XLSX..."):
        df_sal = load_salario_real_xlsx(str(xlsx_path))

    with st.spinner("Lendo PDF e extraindo..."):
        colabs, competencia_global = parse_recibo_pagamento_pdf(
            str(pdf_path),
            use_gpt=usar_gpt,
            openai_model=openai_model
        )

    rows = []
    for c in colabs:
        nome_pdf = c.get("nome") or ""
        ref = find_colaborador_ref(
            df_sal,
            nome=nome_pdf,
            gpt_match_fn=(lambda nome, cands: gpt_disambiguate_name(nome, cands, model=openai_model)) if usar_gpt else None,
        )

        nome = (nome_pdf or ref.get("nome") or "").strip()
        bruto_ref = ref.get("bruto_referencial")

        # cargo por faixa BRUTO PLANILHA (como você pediu)
        depto = (ref.get("departamento") or "").strip()
        cargo_base = (ref.get("cargo") or "").strip()
        familia = infer_familia(f"{depto} {cargo_base}")
        nivel = nivel_por_salario(float(bruto_ref)) if bruto_ref is not None else None
        cargo_plano = cargo_final(familia, nivel) if nivel else None

        eventos = c.get("eventos") or []

        # referência e salário contratual (8781/8786)
        referencia_dias = 30.0
        salario_contratual = 0.0
        for e in eventos:
            cod = str(e.get("codigo") or "").strip()
            pv = float(e.get("provento") or 0.0)
            if cod in ("8781", "8786"):
                salario_contratual += pv
                refv = parse_money_any(e.get("referencia"))
                if refv and 0 < refv <= 31:
                    referencia_dias = float(refv)

        # líquido holerite oficial
        liquido_holerite = c.get("liquido")
        if liquido_holerite is None:
            tv, td = c.get("total_vencimentos"), c.get("total_descontos")
            if tv is not None and td is not None:
                liquido_holerite = float(tv) - float(td)

        valor_a_pagar, regra, bruto_prop = compute_valor_a_pagar(
            bruto_planilha=bruto_ref,
            referencia_dias=referencia_dias,
            salario_contratual_holerite=salario_contratual,
            liquido_holerite=liquido_holerite,
            limiar_zero=limiar_liquido_zero,
        )

        rows.append({
            "page_index": c.get("page_index"),
            "competencia": c.get("competencia") or competencia_global,
            "nome": nome,
            "departamento": depto or None,
            "cargo_plano": cargo_plano,
            "bruto_planilha": bruto_ref,
            "referencia_base_dias": referencia_dias,
            "bruto_planilha_proporcional": bruto_prop,
            "salario_contratual_holerite": salario_contratual,
            "liquido_holerite": liquido_holerite,
            "valor_a_pagar": valor_a_pagar,
            "regra_aplicada": regra,
            "eventos": eventos,
            "holerite_texto": c.get("raw_text") or "",
        })

    df = pd.DataFrame(rows)
    st.session_state["df"] = df
    st.session_state["pdf_bytes"] = pdf_path.read_bytes()
    st.success(f"Processado: {len(df)} colaborador(es).")


df = st.session_state.get("df")
pdf_bytes = st.session_state.get("pdf_bytes")

if df is None:
    st.info("Clique em **Processar** para gerar o demonstrativo.")
    st.stop()

if df.empty:
    st.warning("Nenhum colaborador foi extraído do PDF. Verifique o arquivo e tente novamente.")
    st.stop()

tab1, tab2, tab3 = st.tabs(["Consolidado", "Espelho do Recibo", "Exportações"])

with tab1:
    st.dataframe(df.drop(columns=["eventos", "holerite_texto"], errors="ignore"), width="stretch")

with tab2:
    opts = df.reset_index().to_dict("records")
    sel = st.selectbox("Selecione o colaborador", opts, format_func=lambda r: r.get("nome", ""))
    row = df.loc[sel["index"]]

    cA, cB, cC, cD = st.columns(4)
    cA.metric("Competência", row.get("competencia") or "")
    cB.metric("Bruto planilha (prop.)", row.get("bruto_planilha_proporcional"))
    cC.metric("Salário contratual (8781/8786)", row.get("salario_contratual_holerite"))
    cD.metric("Valor líquido a pagar", row.get("valor_a_pagar"))

    st.caption(
        f"Bruto planilha: {row.get('bruto_planilha')} | Ref dias: {row.get('referencia_base_dias')} | "
        f"Líquido holerite: {row.get('liquido_holerite')} | Regra: {row.get('regra_aplicada')}"
    )

    st.markdown("### Holerite original (imagem)")
    try:
        img = render_pdf_page_image(pdf_bytes, int(row.get("page_index") or 0))
        st.image(img, width="stretch")
    except Exception as e:
        st.warning(f"Não foi possível renderizar a página do PDF: {e}")

    st.markdown("### Eventos (extraídos)")
    st.dataframe(pd.DataFrame(row.get("eventos") or []), width="stretch", hide_index=True)

    with st.expander("Texto bruto (debug)"):
        st.text(row.get("holerite_texto") or "")

with tab3:
    st.write("Exporte o consolidado e os recibos complementares.")
    colA, colB = st.columns(2)

    with colA:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
        tmp.close()
        export_xlsx(df.drop(columns=["eventos", "holerite_texto"], errors="ignore"), tmp.name, logo_path=str(LOGO_PATH) if LOGO_PATH.exists() else None)
        xlsx_bytes = Path(tmp.name).read_bytes()
        Path(tmp.name).unlink(missing_ok=True)

        st.download_button(
            "Baixar Excel (Consolidado)",
            data=xlsx_bytes,
            file_name="consolidado.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    with colB:
        tmpdir = tempfile.TemporaryDirectory()
        pdf_paths = generate_all_receipts(
            df.to_dict("records"),
            out_dir=tmpdir.name,
            empresa_nome=empresa_nome,
            logo_path=str(LOGO_PATH) if LOGO_PATH.exists() else None
        )
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in pdf_paths:
                zf.write(p, arcname=Path(p).name)
        zip_buf.seek(0)

        st.download_button(
            "Baixar Recibos (PDF em ZIP)",
            data=zip_buf.getvalue(),
            file_name="recibos_pdf.zip",
            mime="application/zip",
        )
