# Demonstrativo de Pagamento Contare

App em **Streamlit** para gerar **Recibo Complementar (Extra-folha)** com:

- **Espelho da folha (CLT)**: tabela de eventos/verb as do Recibo de Pagamento
- **Bruto referencial (planilha Excel)**
- **Cálculo do complemento** (diferença) e **valor a pagar**
- Exporta:
  - Excel consolidado
  - Excel de conferência
  - ZIP com PDFs dos recibos complementares

## Como rodar localmente

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy no Streamlit Cloud

1. Suba este repositório no GitHub (preferencialmente **privado**).
2. Crie um app no Streamlit Cloud apontando para `app.py`.
3. (Opcional) Se usar GPT fallback, configure `OPENAI_API_KEY` nos Secrets do app.

## Entradas esperadas

### PDF
- "Recibo de Pagamento" da folha (um ou mais colaboradores no mesmo PDF).
- Idealmente 1 colaborador por página.

### Excel
- Deve conter ao menos **CPF** e uma coluna com o **Bruto referencial**.
- Colunas aceitas (detecção automática): `BRUTO`, `SALARIO`, `SALÁRIO`, `BRUTO REFERENCIAL`, etc.
- Opcional: `STATUS`, `DEPARTAMENTO`, `CARGO`.
