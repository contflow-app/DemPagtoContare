# Demonstrativo de Pagamento Contare

App em Streamlit para geração de **Recibo Complementar (Extra-folha)**,
utilizando o **Recibo de Pagamento (CLT)** como espelho da folha.

## Funcionalidades
- Leitura de Recibo de Pagamento (PDF)
- Extração de eventos (verbas)
- Cálculo de complemento com base em salário referencial (Excel)
- Geração de:
  - Excel consolidado
  - Excel de conferência
  - Recibos complementares em PDF

## Execução local
```bash
pip install -r requirements.txt
streamlit run app.py
```
