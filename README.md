

## 1) Prompt poderoso (copie e cole para a IA/coder)

Contexto do projeto (não revele arquivos confidenciais):  
Tenho um app de batimento CadFi x Controle em Python/Streamlit. O pipeline atual cria três relatórios: “Em Ambos”, “Somente no CadFi” e “Somente no Controle”. Preciso corrigir um bug: **o relatório “Somente no Controle” nunca pode conter fundos cuja situação (SIT) seja diferente de “A”**. Hoje, estão saindo fundos com SIT “I” e “T”.

O que a IA/coder deve fazer (sem acessar meus XLSX):

1) **Manter a coluna de situação (SIT) disponível desde a carga do Controle** até a geração do relatório “Somente no Controle”.  
   - Hoje, a função que carrega o Controle (ex.: `carregar_controle_fic`) só retorna `CNPJ`, `Fundos` e `COD GFI`. Ajuste para **também carregar e propagar a coluna de situação** (ex.: `SIT` ou qualquer outra variação encontrada).  
   - Se preferir, aplique o filtro de situação **antes** de reduzir colunas (i.e., ainda no DataFrame bruto do Controle).

2) **Tornar o detector de coluna de situação mais abrangente**.  
   - A minha função utilitária que encontra a coluna de status/situação (tipo `_encontrar_coluna_status`) deve reconhecer também nomes curtos como **“SIT”** e variações (com/sem acento, minúscula/maiúscula).  
   - Caso encontre códigos de uma letra (A, I, T, P etc.), tratar a primeira letra como o status canônico.

3) **Impor o filtro “apenas ativos” (SIT == 'A')** no ponto certo do fluxo:  
   - **Antes** de calcular “Somente no Controle”, aplique `SIT == 'A'`.  
   - Alternativamente, se preferir manter a lógica atual, **garanta que a coluna de SIT esteja presente** em `df_controle_fora` e que a função `filtrar_controle_por_situacao` seja chamada sobre esse DF com efeito (isto é, ela de fato ache a coluna).

4) **Critérios de aceite (tests a passar):**  
   - Dado um Controle com colunas incluindo “SIT”, ao final, **nenhuma linha de “Somente no Controle”** deve ter SIT diferente de “A”.  
   - Se a coluna de status não existir no arquivo enviado pelo usuário, **não quebre o fluxo**; apenas registre um aviso e prossiga **sem filtrar** (comportamento explícito).  
   - O relatório “Em Ambos” e “Somente no CadFi” **não devem ser alterados** (exceto por eventual reordenação inócua de colunas).  
   - Preserve o formato de `CNPJ` (com máscara) e a coluna `COD GFI` conforme já implementado.

5) **Detalhes do meu código atual para nortear a mudança (sem precisar ver meus arquivos):**  
   - A função `carregar_controle_fic` lê o Controle e **reduz para 3 colunas** — é aqui que a SIT se perde; ampliar para incluir SIT resolve. [1](https://banco365-my.sharepoint.com/personal/t1092497_interno_bb_com_br/Documents/Arquivos%20de%20Microsoft%20Copilot%20Chat/test.py)  
   - O dataset do Controle realmente tem uma coluna chamada **“SIT”** (uma letra por fundo). Logo, o reconhecedor de status precisa contemplar esse nome. [2](https://banco365-my.sharepoint.com/personal/t1092497_interno_bb_com_br/_layouts/15/Doc.aspx?sourcedoc=%7B3C11A22E-4DC9-4D8F-A303-7BF1C064C774%7D&file=CONTROLE%20FIC%20-%20Copia.xlsx&action=default&mobileredirect=true)  
   - No fluxo, calculo `df_controle_fora` por `comparar_controle_fora_cadfi(cadfi_filtrado, controle_prep)` e **só depois** chamo `filtrar_controle_por_situacao(df_controle_fora)`. Se `controle_prep` não trouxer SIT, o filtro não faz nada. [1](https://banco365-my.sharepoint.com/personal/t1092497_interno_bb_com_br/Documents/Arquivos%20de%20Microsoft%20Copilot%20Chat/test.py)

6) **Entregáveis**:  
   - Ajustes pontuais nos utilitários de carga/detecção de coluna de status e no ponto do fluxo onde o filtro é aplicado.  
   - Um resumo do que foi alterado (funções e rationale).  
   - Um mini check-list de testes manuais.

---

## 2) Passo‑a‑passo para você implementar agora (sem código)

### Passo A — Decida **onde** filtrar por SIT == “A”
Opção preferida (robusta): **Filtrar na origem**, logo após ler o Controle e **antes** de reduzir as colunas.  
• Vantagem: você garante que **qualquer** derivado do Controle já nasce apenas com ativos.

### Passo B — **Propagar a coluna de SIT** no `controle_prep`
Hoje, `carregar_controle_fic` retorna só `CNPJ`, `Fundos`, `COD GFI`. Faça com que retorne também **`SIT`** (ou “Situacao” equivalente). Se quiser manter o relatório “limpo”, você pode **remover `SIT` apenas na hora de salvar/exibir**, mas **mantenha-a no DataFrame em memória** até terminar os batimentos. [1](https://banco365-my.sharepoint.com/personal/t1092497_interno_bb_com_br/Documents/Arquivos%20de%20Microsoft%20Copilot%20Chat/test.py)

### Passo C — Tornar o **localizador de status** mais esperto
No utilitário `_encontrar_coluna_status`, inclua estes candidatos no topo:  
`"SIT"`, `"SITUACAO"`, `"SITUAÇÃO"`, `"STATUS"`, `"SITUACAO_DO_FUNDO"`, etc.  
Além disso, mantenha o “score” heurístico, mas **dê prioridade a correspondência exata** com “SIT”, pois é o caso real do seu arquivo. [1](https://banco365-my.sharepoint.com/personal/t1092497_interno_bb_com_br/Documents/Arquivos%20de%20Microsoft%20Copilot%20Chat/test.py)[2](https://banco365-my.sharepoint.com/personal/t1092497_interno_bb_com_br/_layouts/15/Doc.aspx?sourcedoc=%7B3C11A22E-4DC9-4D8F-A303-7BF1C064C774%7D&file=CONTROLE%20FIC%20-%20Copia.xlsx&action=default&mobileredirect=true)

### Passo D — **Normalize** os valores de status
Ao mapear a coluna encontrada:  
• Converta para string, **tire acentos**, **upper()**, e **pegue só a primeira letra**.  
• Isso faz “Ativo”, “Ativa” → “A”; “Inativo” → “I”; “Pendente” → “P”; etc. (Seu helper já faz isso; só garanta que está sendo chamado **sobre a coluna certa**). [1](https://banco365-my.sharepoint.com/personal/t1092497_interno_bb_com_br/Documents/Arquivos%20de%20Microsoft%20Copilot%20Chat/test.py)

### Passo E — Aplique o filtro **antes de “Somente no Controle”**
Caminho 1 (recomendado):  
1) Ao terminar `carregar_controle_fic`, já retorne **apenas linhas com SIT == “A”**.  
2) Continue o fluxo normal: `comparar_*` + relatórios.  
Caminho 2 (alternativo, se quiser mexer menos):  
1) Mantenha `carregar_controle_fic` ampliado com coluna `SIT`.  
2) Logo **antes** de `comparar_controle_fora_cadfi`, aplique o filtro em `controle_prep = filtrar_controle_por_situacao(controle_prep)`.  
Assim, quando `df_controle_fora` for gerado, ele **já** estará restrito a ativos. [1](https://banco365-my.sharepoint.com/personal/t1092497_interno_bb_com_br/Documents/Arquivos%20de%20Microsoft%20Copilot%20Chat/test.py)

### Passo F — **Teste rápido** (3 cenários)
1) **Controle com SIT = “A”, “I”, “T”** misturados:  
   • Esperado: “Somente no Controle” trazer **só** CNPJs com SIT “A”.  
2) **Sem coluna de SIT** (remova a coluna num arquivo de teste):  
   • Esperado: app não quebra; exibe **aviso** e retorna todos (comportamento atual), deixando claro que não foi possível filtrar.  
3) **SIT escrita como texto longo (“Ativo”, “Inativo”)**:  
   • Esperado: normalização por primeira letra funcione e filtre corretamente.

---

## 3) Onde mexer no seu fluxo atual

- **Função `carregar_controle_fic`**:  
  • Passa a incluir a coluna de status (ex.: “SIT”) no DataFrame de saída, em vez de cortar para só 3 colunas. [1](https://banco365-my.sharepoint.com/personal/t1092497_interno_bb_com_br/Documents/Arquivos%20de%20Microsoft%20Copilot%20Chat/test.py)  
  • (Opcional recomendado) Aplique aqui mesmo o filtro `SIT == 'A'`.

- **Função `_encontrar_coluna_status`**:  
  • Adicione “SIT” como palavra‑chave de alta prioridade.  
  • Mantenha o fallback heurístico, mas reconheça “SIT” primeiro. [1](https://banco365-my.sharepoint.com/personal/t1092497_interno_bb_com_br/Documents/Arquivos%20de%20Microsoft%20Copilot%20Chat/test.py)

- **No pipeline do botão “Processar”**:  
  • Garanta que o filtro de situação ocorra **antes** de montar `df_controle_fora` (ou assegure que `df_controle_fora` ainda tenha `SIT` e chame `filtrar_controle_por_situacao` com efeito). Hoje você chama o filtro **depois** de gerar `df_controle_fora`, mas como `controle_prep` perdeu `SIT`, esse filtro não faz nada. [1](https://banco365-my.sharepoint.com/personal/t1092497_interno_bb_com_br/Documents/Arquivos%20de%20Microsoft%20Copilot%20Chat/test.py)


