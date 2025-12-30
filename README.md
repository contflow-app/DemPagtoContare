# Demonstrativo de Pagamento Contare (Base: Extrato Mensal)

Este app (Streamlit) gera Recibos Complementares (Extra-folha) usando o **Extrato Mensal da Folha (PDF)** e a
planilha Excel de **Bruto referencial**.

## Regras
- Padrão: Bruto(planilha) - Líquido(folha)
- Especial (líquido ~0 & ATIVO): Bruto(planilha) - 8781 - 981

## Observação importante (Nomes)
Se o PDF não trouxer o nome corretamente, o app prioriza o **nome da planilha** quando encontra o CPF.
