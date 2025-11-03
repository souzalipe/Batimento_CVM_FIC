def _encontrar_coluna_status(df: pd.DataFrame):
    """
    Localiza a coluna que contém a situação/status no DataFrame.
    Prioriza correspondência exata curta ('sit') e variações comuns.
    Retorna o nome original da coluna (caso sensível à caixa).
    """
    norm_map = {_norm_header_key(c): c for c in df.columns}

    # Prioridade: inclua 'sit' de alta prioridade (coluna curta)
    prioridade = [
        "sit", "situacao", "situacao_do_fundo", "situacao_do_fundo", "situacao",
        "situacao_do_fundo", "situcao", "status", "status_do_fundo"
    ]
    for key in prioridade:
        if key in norm_map:
            return norm_map[key]

    # fallback heurístico (mantém compatibilidade)
    candidatos = []
    for k, original in norm_map.items():
        score = 0
        if "situac" in k or "situa" in k: score += 4
        if k == "sit":                    score += 5
        if "status" in k:                 score += 2
        if "fundo" in k:                  score += 1
        if "cnpj" in k:                   score = -1
        if score > 0:
            candidatos.append((score, original))
    if candidatos:
        candidatos.sort(reverse=True, key=lambda x: x[0])
        return candidatos[0][1]
    return None


