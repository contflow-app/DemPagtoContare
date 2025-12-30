from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.units import mm


def _fmt_money(v) -> str:
    if v is None:
        return "-"
    try:
        s = f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"R$ {s}"
    except Exception:
        return "-"

def _safe(v, default=""):
    return v if v is not None else default

def _draw_box(c: Canvas, x: float, y: float, w: float, h: float, title: Optional[str] = None):
    c.rect(x, y - h, w, h, stroke=1, fill=0)
    if title:
        c.setFont("Helvetica-Bold", 9)
        c.drawString(x + 2*mm, y - 4*mm, title)

def _draw_kv_row(c: Canvas, x: float, y: float, w: float, key: str, val: str):
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(x, y, f"{key}:")
    c.setFont("Helvetica", 8.5)
    c.drawString(x + 28*mm, y, val[:80])

def _draw_table_grid(c: Canvas, x: float, y: float, col_w: List[float], headers: List[str], rows: List[List[str]], row_h: float = 10.5) -> float:
    # Header background line
    c.setFont("Helvetica-Bold", 8)
    cx = x
    for i, h in enumerate(headers):
        c.drawString(cx + 1.2*mm, y - 7.5, h)
        cx += col_w[i]

    # Draw header line
    c.line(x, y - row_h, x + sum(col_w), y - row_h)

    y_cursor = y - row_h
    c.setFont("Helvetica", 7.8)

    for r in rows:
        y_cursor -= row_h
        cx = x
        for i, cell in enumerate(r):
            c.drawString(cx + 1.2*mm, y_cursor + 2.5, (cell or "")[:90])
            cx += col_w[i]
        # row line
        c.line(x, y_cursor, x + sum(col_w), y_cursor)

    # vertical lines
    cx = x
    c.line(x, y, x, y_cursor)  # left border
    for w in col_w:
        cx += w
        c.line(cx, y, cx, y_cursor)
    return y_cursor

def generate_receipt_pdf(row: Dict, out_pdf: str, logo_path: Optional[str] = None, empresa_nome: str = "Contare") -> str:
    c = Canvas(out_pdf, pagesize=A4)
    W, H = A4

    margin = 12*mm
    x0 = margin
    y = H - margin

    # --- Cabeçalho (estilo holerite) ---
    _draw_box(c, x0, y, W - 2*margin, 22*mm)
    if logo_path and Path(logo_path).exists():
        try:
            c.drawImage(logo_path, x0 + 2*mm, y - 18*mm, width=44*mm, height=14*mm, preserveAspectRatio=True, mask="auto")
        except Exception:
            pass

    c.setFont("Helvetica-Bold", 12)
    c.drawString(x0 + 50*mm, y - 8*mm, empresa_nome)
    c.setFont("Helvetica", 9.5)
    c.drawString(x0 + 50*mm, y - 14*mm, "RECIBO COMPLEMENTAR (EXTRA-FOLHA) — COM ESPELHO DA FOLHA")
    comp = _safe(row.get("competencia"), "-")
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(x0 + 160*mm, y - 14*mm, f"Comp.: {comp}")

    y -= 24*mm

    # --- Identificação ---
    _draw_box(c, x0, y, W - 2*margin, 28*mm, "Identificação do Colaborador")
    c.setFont("Helvetica", 8.5)
    _draw_kv_row(c, x0 + 2*mm, y - 10*mm, W - 2*margin, "Nome", str(_safe(row.get("nome"), "-")))
    _draw_kv_row(c, x0 + 2*mm, y - 16*mm, W - 2*margin, "CPF", str(_safe(row.get("cpf"), "-")))
    _draw_kv_row(c, x0 + 90*mm, y - 16*mm, W - 2*margin, "Matrícula", str(_safe(row.get("matricula"), "-")))
    _draw_kv_row(c, x0 + 2*mm, y - 22*mm, W - 2*margin, "Departamento", str(_safe(row.get("departamento"), "-")))
    _draw_kv_row(c, x0 + 90*mm, y - 22*mm, W - 2*margin, "Cargo (plano)", str(_safe(row.get("cargo_plano"), "-")))

    y -= 30*mm

    # --- Espelho da Folha ---
    espelho_h = 120*mm
    _draw_box(c, x0, y, W - 2*margin, espelho_h, "Espelho da Folha (CLT) — Eventos")
    headers = ["Cód", "Descrição", "Ref", "Vencimentos", "Descontos"]
    col_w = [14*mm, 88*mm, 16*mm, 30*mm, 30*mm]

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

    # área interna para tabela
    table_x = x0 + 2*mm
    table_y = y - 8*mm
    # limite de linhas nesta página
    max_lines = 24
    chunk = table_rows[:max_lines]
    y_end = _draw_table_grid(c, table_x, table_y, col_w, headers, chunk, row_h=10.0)

    # Totais no rodapé do box do espelho
    c.setFont("Helvetica-Bold", 8.8)
    c.drawString(x0 + 2*mm, y - espelho_h + 6*mm, f"Total Venc.: {_fmt_money(row.get('total_vencimentos_folha'))}")
    c.drawString(x0 + 58*mm, y - espelho_h + 6*mm, f"Total Desc.: {_fmt_money(row.get('total_descontos_folha'))}")
    c.drawString(x0 + 118*mm, y - espelho_h + 6*mm, f"Líquido: {_fmt_money(row.get('liquido_folha'))}")

    y -= espelho_h + 6*mm

    # Se tiver mais linhas do que coube, criar página 2 com continuação (ainda no estilo holerite)
    if len(table_rows) > max_lines:
        c.showPage()
        y = H - margin
        _draw_box(c, x0, y, W - 2*margin, 18*mm, "Continuação do Espelho da Folha (CLT) — Eventos")
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(x0 + 2*mm, y - 12*mm, f"Colaborador: {str(_safe(row.get('nome'), '-'))}  |  Comp.: {comp}")
        y -= 20*mm

        _draw_box(c, x0, y, W - 2*margin, 170*mm)
        table_x = x0 + 2*mm
        table_y = y - 6*mm
        remaining = table_rows[max_lines:]
        # cabe até ~40 linhas na página 2
        chunk2 = remaining[:40]
        _draw_table_grid(c, table_x, table_y, col_w, headers, chunk2, row_h=10.0)
        y -= 172*mm

    # --- Cálculo do Complemento ---
    _draw_box(c, x0, y, W - 2*margin, 34*mm, "Cálculo do Complemento (Extra-folha)")
    bruto = row.get("bruto_referencial_planilha")
    regra = _safe(row.get("regra_aplicada"), "-")
    v8781 = row.get("verba_8781_salario_contratual")
    v981 = row.get("verba_981_desc_adiantamento")
    val = row.get("valor_a_pagar")

    c.setFont("Helvetica", 9)
    c.drawString(x0 + 2*mm, y - 10*mm, f"Bruto referencial (planilha): {_fmt_money(bruto)}")
    c.drawString(x0 + 2*mm, y - 16*mm, f"Regra aplicada: {regra}")
    if "ESPECIAL" in str(regra):
        c.drawString(x0 + 2*mm, y - 22*mm, f"(-) 8781 Salário contratual: {_fmt_money(v8781)}   (-) 981 Desc. adiantamento: {_fmt_money(v981)}")
    else:
        c.drawString(x0 + 2*mm, y - 22*mm, f"(-) Líquido na folha (CLT): {_fmt_money(row.get('liquido_folha'))}")

    c.setFont("Helvetica-Bold", 12)
    c.drawString(x0 + 2*mm, y - 31*mm, f"VALOR LÍQUIDO A PAGAR (EXTRA-FOLHA): {_fmt_money(val)}")

    y -= 36*mm

    # --- Assinaturas ---
    _draw_box(c, x0, y, W - 2*margin, 22*mm, "Assinaturas")
    c.setFont("Helvetica", 9)
    c.line(x0 + 6*mm, y - 14*mm, x0 + 88*mm, y - 14*mm)
    c.drawString(x0 + 6*mm, y - 18*mm, "Colaborador")
    c.line(x0 + 104*mm, y - 14*mm, x0 + 190*mm, y - 14*mm)
    c.drawString(x0 + 104*mm, y - 18*mm, "Responsável / Empresa")

    c.showPage()
    c.save()
    return out_pdf


def generate_all_receipts(rows: List[Dict], out_dir: str, empresa_nome: str = "Contare", logo_path: Optional[str] = None) -> List[str]:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    pdfs: List[str] = []
    for r in rows:
        cpf = (r.get("cpf") or "").replace(".", "").replace("-", "")
        nome = (r.get("nome") or "COLAB").replace("/", "-").replace(" ", "_")[:30]
        comp = (r.get("competencia") or "MM_AAAA").replace("/", "-")
        filename = f"recibo_complementar_{comp}_{cpf}_{nome}.pdf"
        path = str(Path(out_dir) / filename)
        generate_receipt_pdf(r, path, logo_path=logo_path, empresa_nome=empresa_nome)
        pdfs.append(path)
    return pdfs
