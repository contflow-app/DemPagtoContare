from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Optional
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.units import mm

import pdfplumber
import tempfile
import io

def _fmt(v) -> str:
    try:
        s = f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"R$ {s}"
    except Exception:
        return "-"

def generate_receipt_pdf(row: Dict, out_pdf: str, logo_path: Optional[str] = None, empresa_nome: str = "Contare", holerite_pdf_bytes: Optional[bytes] = None, holerite_page_index: int = 0) -> str:
    c = Canvas(out_pdf, pagesize=A4)
    W, H = A4
    m = 12*mm
    x = m
    y = H - m

    c.rect(x, y-22*mm, W-2*m, 22*mm, stroke=1, fill=0)
    if logo_path and Path(logo_path).exists():
        try:
            c.drawImage(logo_path, x+2*mm, y-18*mm, width=44*mm, height=14*mm, preserveAspectRatio=True, mask="auto")
        except Exception:
            pass
    c.setFont("Helvetica-Bold", 12)
    c.drawString(x+50*mm, y-8*mm, empresa_nome)
    c.setFont("Helvetica", 9)
    c.drawString(x+50*mm, y-14*mm, "RECIBO COMPLEMENTAR (EXTRA-FOLHA)")

    y -= 28*mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x, y, f"Colaborador: {row.get('nome') or '-'}  | CPF: {row.get('cpf') or '-'}")
    y -= 10*mm
    c.setFont("Helvetica", 10)
    c.drawString(x, y, f"Competência: {row.get('competencia') or '-'}")
    y -= 12*mm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(x, y, "Cálculo")
    y -= 8*mm
    c.setFont("Helvetica", 10)

    c.drawString(x, y, f"Remuneração Bruta (planilha): {_fmt(row.get('remuneracao_bruta_planilha') or row.get('bruto_planilha'))}")
    y -= 6*mm
    c.drawString(x, y, f"(+) Outros Proventos: {_fmt(row.get('outros_proventos'))}")
    y -= 6*mm
    c.drawString(x, y, f"(-) Desc. Adiantamento (981): {_fmt(row.get('desc_adiantamento_981') or row.get('981_desc_adiantamento'))}")
    y -= 6*mm
    c.drawString(x, y, f"(-) Desc. INSS (998): {_fmt(row.get('desc_inss_998') or row.get('998_inss'))}")
    y -= 6*mm
    c.drawString(x, y, f"(-) Outros Descontos: {_fmt(row.get('outros_descontos'))}")
    y -= 6*mm
    c.line(x, y, x + 170*mm, y)
    y -= 8*mm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(x, y, f"REMUNERAÇÃO LÍQUIDA A PAGAR: {_fmt(row.get('valor_a_pagar'))}")


    # 2ª página: Espelho do holerite CLT (imagem) — para conferência
    if holerite_pdf_bytes:
        try:
            with pdfplumber.open(io.BytesIO(holerite_pdf_bytes)) as pdf:
                page = pdf.pages[int(holerite_page_index)]
                img = page.to_image(resolution=170).original  # PIL.Image

            c.showPage()
            c.setFont("Helvetica-Bold", 12)
            c.drawString(x, H - m, "HOLERITE ORIGINAL (ESPELHO CLT)")

            max_w = W - 2*m
            max_h = H - 3*m
            iw, ih = img.size
            scale = min(max_w / iw, max_h / ih)
            dw, dh = iw * scale, ih * scale

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                img.save(tf.name, format="PNG")
                png_path = tf.name

            c.drawImage(png_path, x, H - m - dh, width=dw, height=dh, preserveAspectRatio=True, mask="auto")
        except Exception:
            pass

    c.showPage(); c.save()
    return out_pdf

def generate_all_receipts(rows: List[Dict], out_dir: str, empresa_nome: str = "Contare", logo_path: Optional[str] = None, holerite_pdf_bytes: Optional[bytes] = None) -> List[str]:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    out = []
    for r in rows:
        cpf = (r.get("cpf") or "").replace(".", "").replace("-", "")
        nm = (r.get("nome") or "COLAB").replace(" ", "_")[:25]
        comp = (r.get("competencia") or "MM-AAAA").replace("/", "-")
        fn = f"recibo_complementar_{comp}_{cpf}_{nm}.pdf"
        p = str(Path(out_dir) / fn)
        generate_receipt_pdf(r, p, logo_path=logo_path, empresa_nome=empresa_nome, holerite_pdf_bytes=holerite_pdf_bytes, holerite_page_index=int(r.get('page_index') or 0))
        out.append(p)
    return out
