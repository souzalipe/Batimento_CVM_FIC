def carregar_controle_fic(arquivo):
    """
    Lê do Controle FIC as colunas 'Fundos', 'CNPJ', 'COD GFI' e, se existir, propaga também 'SIT' (ou variação).
    Funciona para .xlsx e .xls (precisa de xlrd p/ .xls).
    """
    # 1) Ler tudo como texto (evita depender de letras de coluna)
    ext = str(getattr(arquivo, "name", "")).lower().rsplit(".", 1)[-1]
    engine = "openpyxl" if ext == "xlsx" else None  # deixe None p/ pandas escolher xlrd p/ .xls
    df = pd.read_excel(arquivo, dtype=str, engine=engine)

    # 2) Normalizar cabeçalhos (mesma normalização que havia)
    import unicodedata, re
    def norm(s):
        s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("utf-8")
        s = re.sub(r"\s+", " ", s).strip().upper()
        return s

    colmap = {norm(c): c for c in df.columns}

    # 3) Resolver nomes-alvo com vários candidatos
    def pick(*cands):
        for c in cands:
            if c in colmap:
                return colmap[c]
        return None

    col_fundos = pick("FUNDOS", "FUNDO", "NOME DO FUNDO", "NOME")
    col_cnpj   = pick("CNPJ")
    col_gfi    = pick("COD GFI", "COD_GFI", "CODIGO GFI", "CODIGO_GFI", "GFI")

    # 3.1) Tentar localizar coluna de situação (SIT) — olhar por chaves curtas e variações
    col_sit = None
    for cand in ("SIT", "SITUAÇÃO", "SITUACAO", "SITUACAO_DO_FUNDO", "STATUS", "STATUS_DO_FUNDO"):
        if cand in colmap:
            col_sit = colmap[cand]
            break
    # fallback: procura qualquer header que contenha 'SIT' ou 'SITUAC'
    if not col_sit:
        for k, original in colmap.items():
            if "SIT" in k or "SITUAC" in k:
                col_sit = original
                break

    # 4) Montar saída mínima (propagando SIT se encontrado)
    out = pd.DataFrame()
    if col_cnpj:   out["CNPJ"]   = df[col_cnpj]
    if col_fundos: out["Fundos"] = df[col_fundos]
    if col_gfi:    out["COD GFI"]= df[col_gfi]
    if col_sit:    out["SIT"]    = df[col_sit].astype(str).fillna("")

    # 5) Normalizar CNPJ e tirar duplicatas (mantém lógica atual)
    def so_digitos(s): return re.sub(r"\D", "", str(s or ""))
    def normaliza_cnpj(c):
        d = so_digitos(c)
        if len(d) == 14: return d
        return d.zfill(14) if 0 < len(d) < 14 else None
    def formatar_cnpj(c):
        d = normaliza_cnpj(c)
        if not d: return None
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"

    if "CNPJ" in out.columns:
        out["CNPJ"] = out["CNPJ"].apply(lambda x: formatar_cnpj(normaliza_cnpj(x)) if pd.notna(x) else None)
        out = out.dropna(subset=["CNPJ"]).drop_duplicates(subset=["CNPJ"], keep="first")

    # 6) Garantir colunas (agora incluindo SIT)
    for need in ["CNPJ", "Fundos", "COD GFI", "SIT"]:
        if need not in out.columns:
            out[need] = ""

    # 7) Retornar com SIT no final (se existir) — facilita debug, mas preserva compatibilidade com as 3 colunas
    cols_order = ["CNPJ", "Fundos", "COD GFI"]
    if "SIT" in out.columns:
        cols_order.append("SIT")
    return out[cols_order]
