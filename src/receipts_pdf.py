from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.units import mm

def _fmt_money(v):
    if v is None:
        return "-"
    try:
        s = f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"R$ {s}"
    except Exception:
        return "-"

def _draw_table(c: Canvas, x: float, y: float, col_w: List[float], headers: List[str], rows: List[List[str]], row_h: float = 12.0) -> float:
    # headers
    c.setFont("Helvetica-Bold", 8.5)
    cx = x
    for i, h in enumerate(headers):
        c.drawString(cx, y, h)
        cx += col_w[i]
    y -= row_h
    c.setFont("Helvetica", 8)
    for r in rows:
        cx = x
        for i, cell in enumerate(r):
            c.drawString(cx, y, (cell or "")[:75])
            cx += col_w[i]
        y -= row_h
    return y

def generate_receipt_pdf(row: Dict, out_pdf: str, logo_path: Optional[str] = None, empresa_nome: str = "Contare") -> str:
    c = Canvas(out_pdf, pagesize=A4)
    w, h = A4

    y = h - 18*mm

    # Logo
    if logo_path and Path(logo_path).exists():
        try:
            c.drawImage(logo_path, 18*mm, y-14*mm, width=45*mm, height=14*mm, preserveAspectRatio=True, mask="auto")
        except Exception:
            pass

    c.setFont("Helvetica-Bold", 13)
    c.drawString(70*mm, y, empresa_nome)
    c.setFont("Helvetica", 10)
    c.drawString(70*mm, y-6*mm, "Recibo Complementar (Extra-folha)")
    comp = row.get("competencia") or "-"
    c.drawString(70*mm, y-12*mm, f"Competência: {comp}")

    y -= 24*mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(18*mm, y, "Identificação")
    y -= 7*mm
    c.setFont("Helvetica", 9.5)

    def line(lbl, val):
        nonlocal y
        c.drawString(18*mm, y, f"{lbl}: {val or '-'}")
        y -= 5.8*mm

    line("Nome", row.get("nome"))
    line("CPF", row.get("cpf"))
    line("Matrícula", row.get("matricula"))
    line("Departamento", row.get("departamento"))
    line("Cargo (folha)", row.get("cargo_folha"))
    line("Cargo (plano)", row.get("cargo_plano"))

    y -= 2*mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(18*mm, y, "Espelho da Folha (CLT)")
    y -= 7*mm

    eventos = row.get("eventos_folha") or []
    table_rows = []
    for e in eventos:
        table_rows.append([
            str(e.get("codigo","")),
            str(e.get("descricao","")),
            str(e.get("referencia","") or ""),
            _fmt_money(e.get("vencimentos")),
            _fmt_money(e.get("descontos")),
        ])

    headers = ["Cód", "Descrição", "Ref", "Venc.", "Desc."]
    col_w = [14*mm, 90*mm, 18*mm, 28*mm, 28*mm]

    # Paginação simples
    max_rows_per_page = 26
    idx = 0
    while idx < len(table_rows):
        chunk = table_rows[idx: idx+max_rows_per_page]
        y = _draw_table(c, 18*mm, y, col_w, headers, chunk, row_h=11)
        idx += max_rows_per_page
        if idx < len(table_rows):
            c.showPage()
            y = h - 20*mm

    y -= 4*mm
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(18*mm, y, "Totais da Folha")
    y -= 6*mm
    c.setFont("Helvetica", 9.5)
    c.drawString(18*mm, y, f"Total Vencimentos: {_fmt_money(row.get('total_vencimentos_folha'))}")
    y -= 5.5*mm
    c.drawString(18*mm, y, f"Total Descontos: {_fmt_money(row.get('total_descontos_folha'))}")
    y -= 5.5*mm
    c.drawString(18*mm, y, f"Valor Líquido (folha): {_fmt_money(row.get('liquido_folha'))}")
    y -= 8*mm

    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(18*mm, y, "Cálculo do Complemento")
    y -= 6.5*mm
    c.setFont("Helvetica", 9.5)

    c.drawString(18*mm, y, f"Bruto referencial (planilha): {_fmt_money(row.get('bruto_referencial_planilha'))}")
    y -= 5.5*mm

    regra = row.get("regra_aplicada") or "-"
    c.drawString(18*mm, y, f"Regra aplicada: {regra}")
    y -= 5.5*mm

    if "ESPECIAL" in regra:
        c.drawString(18*mm, y, f"Verba 8781 (salário contratual): {_fmt_money(row.get('verba_8781_salario_contratual'))}")
        y -= 5.5*mm
        c.drawString(18*mm, y, f"Verba 981 (desc adiantamento): {_fmt_money(row.get('verba_981_desc_adiantamento'))}")
        y -= 5.5*mm
    else:
        c.drawString(18*mm, y, f"Líquido (folha): {_fmt_money(row.get('liquido_folha'))}")
        y -= 5.5*mm

    c.setFont("Helvetica-Bold", 11.5)
    c.drawString(18*mm, y, f"VALOR A PAGAR (extra-folha): {_fmt_money(row.get('valor_a_pagar'))}")
    y -= 16*mm

    # Assinaturas
    c.setFont("Helvetica", 9)
    c.line(18*mm, y, 90*mm, y)
    c.drawString(18*mm, y-5*mm, "Assinatura do Colaborador")
    c.line(110*mm, y, 190*mm, y)
    c.drawString(110*mm, y-5*mm, "Responsável / Empresa")

    c.showPage()
    c.save()
    return out_pdf

def generate_all_receipts(rows: List[Dict], out_dir: str, empresa_nome: str = "Contare", logo_path: Optional[str] = None) -> List[str]:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    pdfs = []
    for r in rows:
        cpf = (r.get("cpf") or "").replace(".", "").replace("-", "")
        nome = (r.get("nome") or "COLAB").replace("/", "-").replace(" ", "_")[:30]
        comp = (r.get("competencia") or "MM_AAAA").replace("/", "-")
        filename = f"recibo_complementar_{comp}_{cpf}_{nome}.pdf"
        path = str(Path(out_dir) / filename)
        generate_receipt_pdf(r, path, logo_path=logo_path, empresa_nome=empresa_nome)
        pdfs.append(path)
    return pdfs
