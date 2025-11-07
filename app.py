import io
import re
import unicodedata
from pathlib import Path
import zipfile
import pandas as pd
import streamlit as st
from typing import Optional, Tuple

# === [NOVO BLOCO] Extração de Protocolo e Competência do Balancete ===

# Mapa de meses PT-BR -> número
MESES_PT = {
    "JAN": 1, "JANEIRO": 1,
    "FEV": 2, "FEVEREIRO": 2,
    "MAR": 3, "MARCO": 3, "MARÇO": 3,
    "ABR": 4, "ABRIL": 4,
    "MAI": 5, "MAIO": 5,
    "JUN": 6, "JUNHO": 6,
    "JUL": 7, "JULHO": 7,
    "AGO": 8, "AGOSTO": 8,
    "SET": 9, "SETEMBRO": 9, "SETEM": 9, "SETEMB": 9,
    "OUT": 10, "OUTUBRO": 10,
    "NOV": 11, "NOVEMBRO": 11,
    "DEZ": 12, "DEZEMBRO": 12,
}

# --- Helper robusto para normalizar competência para MM/YYYY
def _normalize_competencia_to_mm_yyyy(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = normaliza_texto(raw).replace(".", "/").replace("-", "/").replace("\\", "/")
    s = s.replace("  ", " ").strip()

    # 1) dd/mm/yyyy -> MM/YYYY
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", s)
    if m:
        dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
        try:
            mm_i = int(mm)
            if 1 <= mm_i <= 12:
                return f"{mm_i:02d}/{int(yyyy)}"
        except Exception:
            pass

    # 2) mm/yyyy -> MM/YYYY
    m = re.search(r"\b(\d{1,2})/(\d{4})\b", s)
    if m:
        mm, yyyy = int(m.group(1)), int(m.group(2))
        if 1 <= mm <= 12:
            return f"{mm:02d}/{yyyy}"

    # 3) abreviação/nome do mês + ano (ex: jun/25, junho/25, jun/2025, JUN/25)
    m = re.search(r"\b([A-ZÇÃÉÀ-ÿ]{3,10})[^\dA-Z]*(\d{2}|\d{4})\b", s, flags=re.I)
    if m:
        mes_txt = normaliza_texto(m.group(1))
        # tenta mapear a palavra inteira, depois os 3 primeiros chars
        mes_num = MESES_PT.get(mes_txt) or MESES_PT.get(mes_txt[:3]) if mes_txt else None
        if mes_num:
            ano_raw = m.group(2)
            ano = int(ano_raw) + 2000 if len(ano_raw) == 2 else int(ano_raw)
            if 1 <= mes_num <= 12:
                return f"{mes_num:02d}/{ano}"

    # 4) AAAA-MM ou AAAA/MM -> MM/YYYY
    m = re.search(r"\b(20\d{2})[\/\-](\d{1,2})\b", s)
    if m:
        ano, mm = int(m.group(1)), int(m.group(2))
        if 1 <= mm <= 12:
            return f"{mm:02d}/{ano}"

    return None

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

     

# === Helpers para validação por data exata OU por mês/ano (compatível com Python 3.9) ===
import re
import pandas as pd
from typing import Optional

# Extrai MM/AAAA de vários formatos comuns
def _extrair_mm_aaaa(valor: Optional[str]) -> Optional[str]:
    if not valor:
        return None
    s = str(valor).strip()

    # 'DD/MM/AAAA' -> MM/AAAA
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(20\d{2})", s)
    if m:
        mm, aaaa = int(m.group(2)), int(m.group(3))
        if 1 <= mm <= 12:
            return f"{mm:02d}/{aaaa}"

    # 'MM/AAAA'
    m = re.fullmatch(r"(\d{1,2})/(20\d{2})", s)
    if m:
        mm, aaaa = int(m.group(1)), int(m.group(2))
        if 1 <= mm <= 12:
            return f"{mm:02d}/{aaaa}"

    # 'AAAA-MM'
    m = re.fullmatch(r"(20\d{2})-(\d{1,2})", s)
    if m:
        aaaa, mm = int(m.group(1)), int(m.group(2))
        if 1 <= mm <= 12:
            return f"{mm:02d}/{aaaa}"

    return None

# === Consolidação e resumo das divergências (compatível com Python 3.9) ===
import pandas as pd
from typing import Optional, Dict

def consolidar_incons_por_fundo(df_incons: pd.DataFrame) -> pd.DataFrame:
    """
    Converte o DF de inconsistências (uma linha por origem) em um DF consolidado (uma linha por CNPJ),
    com colunas lado a lado para CDA e Balancete.
    """
    cols_base = {"CNPJ", "Nome do fundo", "Origem", "Competência atual", "Competência esperada"}
    if not cols_base.issubset(df_incons.columns):
        return pd.DataFrame(columns=[
            "CNPJ","Nome do fundo","CDA atual","CDA esperada","Balancete atual","Balancete esperada"
        ])

    cda = (
        df_incons[df_incons["Origem"]=="CDA"]
        [["CNPJ","Nome do fundo","Competência atual","Competência esperada"]]
        .rename(columns={"Competência atual":"CDA atual","Competência esperada":"CDA esperada"})
    )
    bal = (
        df_incons[df_incons["Origem"]=="Balancete"]
        [["CNPJ","Nome do fundo","Competência atual","Competência esperada"]]
        .rename(columns={"Competência atual":"Balancete atual","Balancete esperada":"Balancete esperada"})
    )

    # OBS: Corrige um rename que o Python não faria automaticamente
    if "Balancete esperada" not in bal.columns:
        bal = bal.rename(columns={"Competência esperada":"Balancete esperada"})

    full = pd.merge(cda, bal, on=["CNPJ","Nome do fundo"], how="outer")
    # Ordena e garante as colunas na ordem desejada
    for col in ["CDA atual","CDA esperada","Balancete atual","Balancete esperada"]:
        if col not in full.columns:
            full[col] = pd.NA

    return full[["CNPJ","Nome do fundo","CDA atual","CDA esperada","Balancete atual","Balancete esperada"]] \
             .drop_duplicates(subset=["CNPJ"]) \
             .sort_values(by=["CNPJ"])

def resumo_divergencias(df_incons: pd.DataFrame, df_base: pd.DataFrame) -> Dict[str, int]:
    """
    Retorna métricas resumidas:
      - total_fundos: nº de CNPJs na base
      - linhas: nº de linhas no relatório de inconsistências (CDA + Balancete)
      - fundos_com_erro: nº de CNPJs únicos com qualquer divergência
      - somente_cda / somente_balancete / ambos: nº de CNPJs por segmento
    """
    total_fundos = df_base["CNPJ"].dropna().nunique() if "CNPJ" in df_base.columns else 0
    linhas = len(df_incons)

    if linhas == 0:
        return {
            "total_fundos": total_fundos,
            "linhas": 0,
            "fundos_com_erro": 0,
            "somente_cda": 0,
            "somente_balancete": 0,
            "ambos": 0,
        }

    cnpjs_cda = set(df_incons[df_incons["Origem"]=="CDA"]["CNPJ"].dropna())
    cnpjs_bal = set(df_incons[df_incons["Origem"]=="Balancete"]["CNPJ"].dropna())
    fundos_com_erro = len(cnpjs_cda | cnpjs_bal)
    ambos = len(cnpjs_cda & cnpjs_bal)
    somente_cda = len(cnpjs_cda - cnpjs_bal)
    somente_balancete = len(cnpjs_bal - cnpjs_cda)

    return {
        "total_fundos": total_fundos,
        "linhas": linhas,
        "fundos_com_erro": fundos_com_erro,
        "somente_cda": somente_cda,
        "somente_balancete": somente_balancete,
        "ambos": ambos,
    }


# Normaliza uma string data 'qualquer' para DD/MM/AAAA quando possível (mantém "Não possui")
def _coagir_para_dd_mm_aaaa(valor: Optional[str]) -> Optional[str]:
    if valor is None:
        return None
    t = str(valor).strip()
    if not t or t.upper() == "NÃO POSSUI":
        return t

    # Já está em DD/MM/AAAA válido
    m = re.fullmatch(r"(\d{2})/(\d{2})/(20\d{2})", t)
    if m:
        return t

    # Se veio MM/AAAA ou AAAA-MM, força dia 01
    mm_aaaa = _extrair_mm_aaaa(t)
    if mm_aaaa:
        mm, aaaa = mm_aaaa.split("/")
        return f"01/{mm}/{aaaa}"

    # Sem reconhecer: devolve como veio
    return t

def validar_por_data_exata(
    df: pd.DataFrame,
    data_alvo_ddmmaaaa: str,
    contar_nao_possui: bool = True
) -> pd.DataFrame:
    """
    Compara se CDA_Competencia e Balancete_Competencia == data_alvo (DD/MM/AAAA) exatamente.
    Retorna apenas as inconsistências.
    """
    # Sanitiza a data alvo (aceita 1/8/2025, 01/8/2025, etc.)
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(20\d{2})\s*", str(data_alvo_ddmmaaaa))
    if not m:
        raise ValueError("Data inválida. Use o formato DD/MM/AAAA.")
    dd, mm, aaaa = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        raise ValueError("Data inválida. Verifique dia e mês.")
    data_alvo = f"{dd:02d}/{mm:02d}/{aaaa}"

    inconsistencias = []
    col_nome = None
    for c in ("Nome do fundo", "Denominacao_Social", "Denominacao Social", "Denominacao"):
        if c in df.columns:
            col_nome = c
            break

    for col, origem in (("CDA_Competencia", "CDA"), ("Balancete_Competencia", "Balancete")):
        if col not in df.columns:
            continue
        for _, row in df.iterrows():
            atual = row.get(col)
            if atual is None:
                continue
            if str(atual).strip().upper() == "NÃO POSSUI":
                if contar_nao_possui:
                    inconsistencias.append({
                        "CNPJ": row.get("CNPJ"),
                        "Nome do fundo": (row.get(col_nome) if col_nome else None),
                        "Origem": origem,
                        "Competência atual": atual,
                        "Competência esperada": data_alvo
                    })
                continue

            atual_norm = _coagir_para_dd_mm_aaaa(atual)
            if atual_norm != data_alvo:
                inconsistencias.append({
                    "CNPJ": row.get("CNPJ"),
                    "Nome do fundo": (row.get(col_nome) if col_nome else None),
                    "Origem": origem,
                    "Competência atual": atual,
                    "Competência esperada": data_alvo
                })

    return pd.DataFrame(inconsistencias)

def validar_por_mes_ano(
    df: pd.DataFrame,
    mes_ano_alvo: str,  # "MM/AAAA"
    contar_nao_possui: bool = True
) -> pd.DataFrame:
    """
    Compara apenas MM/AAAA das colunas CDA_Competencia e Balancete_Competencia.
    Retorna apenas as inconsistências.
    """
    m = re.fullmatch(r"\s*(\d{1,2})/(20\d{2})\s*", str(mes_ano_alvo))
    if not m:
        raise ValueError("Mês/Ano inválido. Use o formato MM/AAAA.")
    mm, aaaa = int(m.group(1)), int(m.group(2))
    if not (1 <= mm <= 12):
        raise ValueError("Mês inválido (1-12).")
    alvo_mm_aaaa = f"{mm:02d}/{aaaa}"

    inconsistencias = []
    col_nome = None
    for c in ("Nome do fundo", "Denominacao_Social", "Denominacao Social", "Denominacao"):
        if c in df.columns:
            col_nome = c
            break

    for col, origem in (("CDA_Competencia", "CDA"), ("Balancete_Competencia", "Balancete")):
        if col not in df.columns:
            continue
        for _, row in df.iterrows():
            atual = row.get(col)
            if atual is None:
                continue
            if str(atual).strip().upper() == "NÃO POSSUI":
                if contar_nao_possui:
                    inconsistencias.append({
                        "CNPJ": row.get("CNPJ"),
                        "Nome do fundo": (row.get(col_nome) if col_nome else None),
                        "Origem": origem,
                        "Competência atual": atual,
                        "Competência esperada": f"Qualquer dia/{alvo_mm_aaaa}"
                    })
                continue

            mm_aaaa = _extrair_mm_aaaa(str(atual))
            if mm_aaaa != alvo_mm_aaaa:
                inconsistencias.append({
                    "CNPJ": row.get("CNPJ"),
                    "Nome do fundo": (row.get(col_nome) if col_nome else None),
                    "Origem": origem,
                    "Competência atual": str(atual),
                    "Competência esperada": f"Qualquer dia/{alvo_mm_aaaa}"
                })

    return pd.DataFrame(inconsistencias)


# === Helpers para validação de dia da competência (compatível com Python 3.9) ===
import re
import pandas as pd
from typing import Optional

def _extrair_mm_aaaa(valor: str) -> Optional[str]:
    if not valor:
        return None
    s = str(valor).strip()
    # aceita 'DD/MM/AAAA'
    m = re.fullmatch(r"(\d{2})/(\d{2})/(20\d{2})", s)
    if m:
        return f"{m.group(2)}/{m.group(3)}"
    # aceita 'MM/AAAA'
    m = re.fullmatch(r"(\d{2})/(20\d{2})", s)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    # aceita 'AAAA-MM'
    m = re.fullmatch(r"(20\d{2})-(\d{1,2})", s)
    if m:
        mm = int(m.group(2))
        if 1 <= mm <= 12:
            return f"{mm:02d}/{m.group(1)}"
    return None

def _ajustar_dia_competencia(valor: Optional[str], dia: int) -> Optional[str]:
    """Gera 'DD/MM/AAAA' usando o MM/AAAA detectado no valor e o dia informado.
       Mantém 'Não possui' igual."""
    if valor is None:
        return None
    t = str(valor).strip()
    if not t:
        return t
    if t.upper() == "NÃO POSSUI":
        return t
    mm_aaaa = _extrair_mm_aaaa(t)
    if not mm_aaaa:
        return t  # devolve como veio se não der p/ extrair mês/ano
    mm, aaaa = mm_aaaa.split("/")
    return f"{int(dia):02d}/{mm}/{aaaa}"

def validar_competencias_por_dia(df: pd.DataFrame, dia: int, contar_nao_possui: bool = True) -> pd.DataFrame:
    """Retorna um DF apenas com as inconsistências (CNPJ, Origem, Competência atual, Competência esperada)."""
    inconsistencias = []
    col_nome = None
    for c in ("Nome do fundo", "Denominacao_Social", "Denominacao Social", "Denominacao"):
        if c in df.columns:
            col_nome = c
            break

    for col, origem in (("CDA_Competencia", "CDA"), ("Balancete_Competencia", "Balancete")):
        if col not in df.columns:
            continue
        for _, row in df.iterrows():
            atual = row.get(col)
            if atual is None:
                continue
            if str(atual).strip().upper() == "NÃO POSSUI" and not contar_nao_possui:
                continue
            esperado = _ajustar_dia_competencia(atual, dia)
            # Divergência quando strings não batem exatamente
            if atual != esperado:
                inconsistencias.append({
                    "CNPJ": row.get("CNPJ"),
                    "Nome do fundo": (row.get(col_nome) if col_nome else None),
                    "Origem": origem,
                    "Competência atual": atual,
                    "Competência esperada": esperado
                })
    return pd.DataFrame(inconsistencias)


def adicionar_drive_por_cnpj(
    df_base: pd.DataFrame,
    controle_df: pd.DataFrame,
    nome_col_saida: str = "COD GFI"  # <- agora o nome padrão é 'COD GFI'
) -> pd.DataFrame:
    """
    Anexa a coluna 'COD GFI' aos relatórios do primeiro batimento.
    Faz merge por CNPJ com a planilha de Controle (usando a coluna 'COD GFI').
    Se não encontrar a coluna no Controle, devolve o DF original + coluna vazia.
    """
    if df_base is None or df_base.empty:
        return df_base

    # Garante CNPJ formatado dos dois lados
    left = df_base.copy()
    if "CNPJ" in left.columns:
        left["CNPJ"] = left["CNPJ"].apply(
            lambda x: formatar_cnpj(normaliza_cnpj(x)) if pd.notna(x) else None
        )

    right = controle_df.copy()
    if "CNPJ" in right.columns:
        right["CNPJ"] = right["CNPJ"].apply(
            lambda x: formatar_cnpj(normaliza_cnpj(x)) if pd.notna(x) else None
        )

    col_codgfi = "COD GFI"
    if col_codgfi not in right.columns:
        out = left.copy()
        out[nome_col_saida] = ""
        return out

    mapa = (
        right[["CNPJ", col_codgfi]]
        .drop_duplicates(subset="CNPJ", keep="first")
    )

    out = left.merge(mapa, on="CNPJ", how="left")
    if col_codgfi in out.columns:
        out[col_codgfi] = out[col_codgfi].fillna("")

    return out




def _format_competencia_yyyy_mm(ano: int, mes: int) -> str:
    mes = max(1, min(12, int(mes)))
    return f"{int(ano):04d}-{mes:02d}"

def _parse_competencia(texto: str) -> Optional[str]:
    T = normaliza_texto(texto)

    # 1) MM/AAAA ou MM-AAAA
    m = re.search(r"\b(\d{1,2})[/\-](\d{4})\b", T)
    if m:
        mes, ano = int(m.group(1)), int(m.group(2))
        if 1 <= mes <= 12:
            return _format_competencia_yyyy_mm(ano, mes)

    # 2) AAAA-MM ou AAAA/MM
    m = re.search(r"\b(\d{4})[/\-](\d{1,2})\b", T)
    if m:
        ano, mes = int(m.group(1)), int(m.group(2))
        if 1 <= mes <= 12:
            return _format_competencia_yyyy_mm(ano, mes)

    # 3) Nome do mês (abreviado ou completo) + AAAA
    m = re.search(r"\b([A-ZÇÃÉ]+)[\s/.\-]*(\d{4})\b", T)
    if m:
        mes_txt, ano = m.group(1), int(m.group(2))
        mes = MESES_PT.get(mes_txt)
        if mes:
            return _format_competencia_yyyy_mm(ano, mes)

    return None

def _eh_cnpj_sequencia(numeros: str) -> bool:
    d = re.sub(r"\D", "", str(numeros or ""))
    return len(d) == 14

def _parse_protocolo(texto: str) -> Optional[str]:
    T = normaliza_texto(texto)

    m = re.search(r"(?:PROTOCOLO|NUMERO\s*DE\s*PROTOCOLO|GFI)\D*(\d{6,})", T, flags=re.I)
    if m:
        valor = m.group(1)
        if not _eh_cnpj_sequencia(valor):
            return valor

    candidatos = re.findall(r"\b(\d{6,})\b", T)
    candidatos = [c for c in candidatos if not _eh_cnpj_sequencia(c)]
    if candidatos:
        return max(candidatos, key=len)

    return None

def _read_text_from_xlsx(uploaded_file) -> str:
    try:
        df_head = pd.read_excel(uploaded_file, header=None, nrows=40, dtype=str, engine="openpyxl")
        texto = " ".join(df_head.astype(str).fillna("").values.ravel())
        return texto
    except Exception:
        return ""
    finally:
        try:
            uploaded_file.seek(0)
        except Exception:
            pass

def _read_text_from_pdf(uploaded_file) -> str:
    try:
        import fitz  # PyMuPDF
    except Exception:
        return ""

    try:
        data = uploaded_file.read()
        texto = ""
        with fitz.open(stream=data, filetype="pdf") as doc:
            for page in doc:
                texto += " " + page.get_text("text")
        return texto
    except Exception:
        return ""
    finally:
        try:
            uploaded_file.seek(0)
        except Exception:
            pass

def extrair_protocolo_e_competencia_do_balancete(uploaded_file) -> Tuple[Optional[str], Optional[str]]:
    if not uploaded_file:
        return (None, None)

    nome = str(getattr(uploaded_file, "name", "")).lower()
    texto = ""

    if nome.endswith(".xlsx"):
        texto = _read_text_from_xlsx(uploaded_file)
    elif nome.endswith(".pdf"):
        texto = _read_text_from_pdf(uploaded_file)

    texto_total = f"{texto}  {getattr(uploaded_file, 'name', '')}"

    protocolo = _parse_protocolo(texto_total)
    competencia = _parse_competencia(texto_total)

    return (protocolo, competencia)
# === [FIM DO BLOCO NOVO] ===

def so_digitos(s):
    return re.sub(r'\D', '', str(s or ''))

def normaliza_cnpj(cnpj):
    d = so_digitos(cnpj)
    if len(d) == 14:
        return d
    if 0 < len(d) < 14:
        return d.zfill(14)
    return None

def formatar_cnpj(cnpj):
    d = normaliza_cnpj(cnpj)
    if not d or len(d) != 14:
        return None
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"

def remover_duplicatas_por_cnpj(df, coluna_origem):
    df = df.copy()
    df["CNPJ_Normalizado"] = df[coluna_origem].apply(normaliza_cnpj)
    df["CNPJ"] = df["CNPJ_Normalizado"].apply(formatar_cnpj)
    df = df[df["CNPJ"].notnull()]
    return df.drop_duplicates(subset="CNPJ").copy()

def padronizar_colunas(df):
    df = df.copy()
    def norm(s):
        s = unicodedata.normalize("NFKD", str(s))
        s = s.encode("ascii", "ignore").decode("utf-8")
        s = s.strip()
        return s
    df.columns = [norm(c) for c in df.columns]
    return df

def normaliza_texto(s):
    s = str(s or "")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("utf-8")
    return s.strip().upper()

def _norm_header_key(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("utf-8")
    s = re.sub(r"\s+", " ", s.strip().lower())
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s

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




VALORES_ATIVOS = {
    normaliza_texto("Em Funcionamento Normal"),
    normaliza_texto("Em Funcionamento"),
    normaliza_texto("Ativo"),
    normaliza_texto("Ativa"),
    normaliza_texto("Em Atividade"),
    normaliza_texto("A"),
}

def filtrar_status_ativos(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    col = _encontrar_coluna_status(df)
    if not col:
        return df
    out = df.copy()
    out["_STATUS_NORM_"] = out[col].map(normaliza_texto)
    out = out[out["_STATUS_NORM_"].isin(VALORES_ATIVOS)].drop(columns=["_STATUS_NORM_"])
    return out

def carregar_excel(arquivo):
    df = pd.read_excel(arquivo, engine="openpyxl", dtype=str)
    return padronizar_colunas(df)

import re

def filtrar_cadfi(df):
    required = ["Administrador", "Situacao", "Tipo_Fundo", "Denominacao_Social", "CNPJ_Fundo"]
    if not all(col in df.columns for col in required):
        faltantes = set(required) - set(df.columns)
        raise ValueError(f"Colunas ausentes no CadFi: {faltantes}")

    # --- Filtro POSITIVO: qualquer um dos termos (case-insensitive) ---
    termos_incluir = [
        "FIC",             # termo genérico
        "cotas",
        "FIC de FI",
        "FIF FIF",
        "fic de fi",
        "fi de fic",
        "FC",              # se realmente quiser considerar 'FC' como indicativo
        "fc",
    ]
    # Cria um regex do tipo (FIC|cotas|FIC de FI|...)
    padrao_incluir = "(" + "|".join(map(re.escape, termos_incluir)) + ")"

    # --- Filtro de EXCLUSÃO: nomes específicos para remover ---
    nomes_excluir = [
        "BB TOP DI RENDA FIXA REFERENCIADO DI LONGO PRAZO FIC FIF RESPONSABILIDADE LIMITADA",
        "BB PRATA FUNDO DE INVESTIMENTO EM COTAS DE FUNDOS DE INVESTIMENTO FINANCEIRO MULTIMERCADO",
        "BB DIVERSIFICAÇÃO FUNDO MÚTUO DE PRIVATIZAÇÃO - FGTS CARTEIRA LIVRE RESPONSABILIDADE LIMITADA",
        "BB ASSET RENDA FIXA SIMPLES FUNDO DE INVESTIMENTO EM COTAS DE FUNDOS DE INVESTIMENTO FINANCEIRO RESPONSABILIDADE LIMITADA",
    ]
    padrao_excluir = "(" + "|".join(map(re.escape, nomes_excluir)) + ")"

    filtro = (
        (df["Administrador"].fillna("") == "BB GESTAO DE RECURSOS DTVM S.A")
        & (df["Situacao"] == "Em Funcionamento Normal")
        & (df["Tipo_Fundo"] == "FI")
        & (df["Denominacao_Social"].str.contains(padrao_incluir, case=False, na=False, regex=True))
        & (~df["Denominacao_Social"].str.contains(padrao_excluir, case=False, na=False, regex=True))
    )

    df_filtrado = df.loc[filtro].copy()
    return remover_duplicatas_por_cnpj(df_filtrado, "CNPJ_Fundo")

def comparar_controle_fora_cadfi(cadfi_df, controle_df):
    return controle_df[~controle_df["CNPJ"].isin(set(cadfi_df["CNPJ"]))].copy()

def _encontrar_coluna_nome(df: pd.DataFrame) -> str:
    norm_map = {_norm_header_key(c): c for c in df.columns}
    prioridade = [
        "denominacao_social", "denominacao_do_fundo", "denominacao",
        "nome_do_fundo", "nome_fundo", "nome",
        "razao_social", "razao", "descricao"
    ]
    for key in prioridade:
        if key in norm_map:
            return norm_map[key]
    candidatos = []
    for k, original in norm_map.items():
        score = 0
        if "denomin" in k: score += 3
        if "nome" in k:    score += 2
        if "fundo" in k:   score += 1
        if "cnpj" in k:    score = -1
        if score > 0:
            candidatos.append((score, original))
    if candidatos:
        candidatos.sort(reverse=True, key=lambda x: x[0])
        return candidatos[0][1]
    for c in df.columns:
        if c != "CNPJ" and df[c].dtype == object:
            return c
    return None

def relatorio_controle_fora_cadfi(df_controle: pd.DataFrame) -> pd.DataFrame:
    if df_controle is None or df_controle.empty:
        return pd.DataFrame(columns=["CNPJ", "Nome do fundo (Controle)"])
    out = df_controle.copy()
    col_nome = _encontrar_coluna_nome(out)
    if col_nome and col_nome in out.columns:
        out = out.rename(columns={col_nome: "Nome do fundo (Controle)"})
        out["Nome do fundo (Controle)"] = (
            out["Nome do fundo (Controle)"]
            .astype(str)
            .str.strip()
        )
    else:
        out["Nome do fundo (Controle)"] = ""
    return out[["CNPJ", "Nome do fundo (Controle)"]]

EXCLUIR_NOMES_CONTROLE = [
    "BB CIN",
    "BB BNC AÇÕES NOSSA CAIXA NOSSO CLUBE DE INVESTIMENTO",
]

def filtrar_controle_por_nome(df: pd.DataFrame,
                              nomes_excluir=EXCLUIR_NOMES_CONTROLE) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    col_nome = _encontrar_coluna_nome(df)
    if not col_nome or col_nome not in df.columns:
        return df
    nomes_norm = [normaliza_texto(n) for n in nomes_excluir]
    out = df.copy()
    out["_NOME_NORM_"] = out[col_nome].map(normaliza_texto)
    mask_excluir = out["_NOME_NORM_"].apply(lambda s: any(p in s for p in nomes_norm))
    out = out[~mask_excluir].drop(columns=["_NOME_NORM_"])
    return out

EXCLUIR_SITUACAO_CONTROLE = ("I", "P", "T")

def filtrar_controle_por_situacao(df: pd.DataFrame,
                                  excluir_codigos=EXCLUIR_SITUACAO_CONTROLE) -> pd.DataFrame:
    if df is None or df.empty:   # ✅ corrigido     755+ 105 + 84
        return df

    col_status = _encontrar_coluna_status(df)
    if not col_status or col_status not in df.columns:
        return df

    excluir_norm = {normaliza_texto(x)[:1] for x in excluir_codigos}
    out = df.copy()
    out["SIT"] = out[col_status].map(
        lambda x: normaliza_texto(x)[:1] if pd.notna(x) else ""
    )
    mask_excluir = out["SIT"].isin(excluir_norm)
    out = out[~mask_excluir].drop(columns=["SIT"])
    return out


def carregar_controle(df_controle):
    if "CNPJ" not in df_controle.columns:
        raise ValueError("Coluna 'CNPJ' ausente no Controle Espelho.")
    return remover_duplicatas_por_cnpj(df_controle, "CNPJ")

def comparar_cnpjs(cadfi_df, controle_df):
    return cadfi_df[~cadfi_df["CNPJ"].isin(set(controle_df["CNPJ"]))].copy()

def comparar_fundos_em_comum(cadfi_df, controle_df):
    return cadfi_df[cadfi_df["CNPJ"].isin(set(controle_df["CNPJ"]))].copy()

def relatorio_fora_controle(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=["CNPJ", "Nome do fundo"])
    df = df.copy()
    rel = df[["CNPJ", "Denominacao_Social"]].rename(columns={
        "Denominacao_Social": "Nome do fundo",
    })
    return rel

def relatorio_em_comum(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=[
            "CNPJ", "Nome do fundo"
        ])
    df = df.copy()
    rel = df[[
        "CNPJ", "Denominacao_Social"
    ]].rename(columns={
        "Denominacao_Social": "Nome do fundo",
    })
    return rel

def to_excel_bytes(df, sheet_name="Relatorio"):
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    buffer.seek(0)
    return buffer

# ======================= /CDA =====================================================

def _normaliza_competencia_mm_aaaa(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.strip()
    m_iso = re.search(r'(20\d{2})[/\-](\d{2})', s)
    if m_iso:
        ano, mes = int(m_iso.group(1)), int(m_iso.group(2))
        if 1 <= mes <= 12:
            return _format_competencia_yyyy_mm(ano, mes)
    m_br = re.search(r'(\d{2})[/\-](20\d{2})', s)
    if m_br:
        mes, ano = int(m_br.group(1)), int(m_br.group(2))
        if 1 <= mes <= 12:
            return _format_competencia_yyyy_mm(ano, mes)
    return None

def remover_segundos_colunas(df: pd.DataFrame, colunas, formato: str = "%Y-%m-%d %H:%M") -> pd.DataFrame:
    df = df.copy()
    for col in colunas:
        if col in df.columns:
            s = pd.to_datetime(df[col], errors="coerce")
            df.loc[s.notna(), col] = s[s.notna()].dt.strftime(formato)
            df.loc[s.isna(), col] = (
                df.loc[s.isna(), col]
                .astype(str)
                .str.replace(r":\d{2}(?=\b)", "", regex=True)
            )
    return df

def _competencia_to_01_mm_aaaa(s: Optional[str]) -> Optional[str]:
    """Converte '2025-08', '08/2025' ou 'dd/mm/aaaa' para '01/MM/AAAA'.
    Mantém 'Não possui' e vazios como vieram.
    """
    if s is None:
        return None
    t = str(s).strip()
    if not t or t.upper() == "NÃO POSSUI":
        return t

    # AAAA-MM -> 01/MM/AAAA
    m = re.fullmatch(r"(20\d{2})-(\d{1,2})", t)
    if m:
        ano, mes = int(m.group(1)), int(m.group(2))
        if 1 <= mes <= 12:
            return f"01/{mes:02d}/{ano}"

    # MM/AAAA -> 01/MM/AAAA
    m = re.fullmatch(r"(\d{1,2})/(20\d{2})", t)
    if m:
        mes, ano = int(m.group(1)), int(m.group(2))
        if 1 <= mes <= 12:
            return f"01/{mes:02d}/{ano}"

    # DD/MM/AAAA -> força dia 01
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(20\d{2})", t)
    if m:
        dd, mm, ano = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mm <= 12:
            return f"01/{mm:02d}/{ano}"

    # Não casou? mantém como veio (antes retornava None)
    return t


def parse_protocolos_cda_xlsx(arquivo_xlsx) -> pd.DataFrame:
    df_raw = pd.read_excel(arquivo_xlsx, sheet_name=0, header=None, dtype=str)
    lines = []
    for _, row in df_raw.iterrows():
        for val in row.values:
            if pd.isna(val):
                continue
            txt = str(val).strip()
            if txt:
                lines.append((len(lines), txt))

    n = len(lines)
    registros = []
    for i in range(n):
        _, text = lines[i]
        low = text.lower()

        # Âncora: "Nº Protocolo"
        if low.startswith('nº protocolo') or low.startswith('n° protocolo') or low.startswith('no protocolo') \
           or low.startswith('nº do protocolo') or low.startswith('n° do protocolo'):
            # Protocolo (na linha logo abaixo)
            protocolo = None
            j = i + 1
            while j < n:
                _, t2 = lines[j]
                low2 = t2.lower()
                if re.match(
                    r'^(protocolo de confirma|status:|informe:|opera|documento:|compet|usuário|usuario|nº do recebimento|nome do arquivo|participante:|tipo do participante|data ação:|data acao:)',
                    low2
                ):
                    j += 1
                    continue
                protocolo = t2.strip()
                if protocolo.endswith(".0"):
                    protocolo = protocolo[:-2]
                break
                j += 1

            # Participante -> extrair CNPJ (mesma lógica atual)
            cnpj_masked, participante = None, None
            k = i
            while k >= 0:
                _, tprev = lines[k]
                lowp = tprev.lower()
                if lowp.startswith('participante'):
                    first_name = None
                    kk = k + 1
                    while kk < n:
                        _, tline = lines[kk]
                        low2 = tline.lower()
                        if low2.startswith('tipo do participante') or low2.startswith('data ação') or low2.startswith('data acao') \
                           or low2.startswith('nº protocolo') or low2.startswith('n° protocolo') or low2.startswith('nº do protocolo') or low2.startswith('n° do protocolo'):
                            break
                        if first_name is None and tline:
                            first_name = tline.strip()
                        m = re.search(r'(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})', tline)
                        if m:
                            cnpj_masked = m.group(1)
                            break
                        kk += 1
                    participante = first_name
                    break
                k -= 1

            # Competência (igual ao seu, normalizando)
            competencia_raw, data_acao_raw = None, None

            k = i
            while k >= 0:
                _, tprev = lines[k]
                lowp = tprev.lower()
                if lowp.startswith('competência:') or lowp.startswith('competencia:'):
                    kk = k + 1
                    while kk < n:
                        _, tval = lines[kk]
                        if tval:
                            competencia_raw = tval.strip()
                            break
                        kk += 1
                    break
                k -= 1

            k = i
            while k >= 0:
                _, tprev = lines[k]
                lowp = tprev.lower()
                if lowp.startswith('data ação') or lowp.startswith('data acao'):
                    kk = k + 1
                    while kk < n:
                        _, tval = lines[kk]
                        if tval:
                            data_acao_raw = tval.strip()
                            break
                        kk += 1
                    break
                k -= 1

            # 🔎 Status (NOVO): pegar a linha logo após "Status:"
            status_txt = None
            k = i
            while k >= 0:
                _, tprev = lines[k]
                lowp = tprev.lower()
                if lowp.startswith('status:'):
                    kk = k + 1
                    while kk < n:
                        _, tval = lines[kk]
                        if tval:
                            status_txt = tval.strip()
                            break
                        kk += 1
                    break
                k -= 1

            if cnpj_masked and protocolo:
                cnpj_num = normaliza_cnpj(cnpj_masked)
                comp = _normaliza_competencia_mm_aaaa(competencia_raw)

                try:
                    data_acao = pd.to_datetime(data_acao_raw, dayfirst=True, errors='coerce') if data_acao_raw else pd.NaT
                except Exception:
                    data_acao = pd.NaT

                registros.append({
                    "CNPJ_Masked": cnpj_masked,
                    "CNPJ_Num": cnpj_num,
                    "Participante": participante,
                    "CDA_Protocolo": protocolo,
                    "CDA_Competencia": comp,
                    "CDA_Status": status_txt or "",
                    "Data_Acao": data_acao
                })

    df = pd.DataFrame(registros)
    if df.empty:
        return df

    # Mantém a lógica: um por CNPJ, priorizando Data_Acao mais recente
    df = df.sort_values(["CNPJ_Num", "Data_Acao"], ascending=[True, False]) \
           .drop_duplicates("CNPJ_Num", keep="first")

    return df

def enriquecer_em_comum_com_cda(rel_em_comum_df: pd.DataFrame, df_cda: pd.DataFrame) -> pd.DataFrame:
    # Se o relatório base estiver ausente, devolve DF vazio, nunca None
    if rel_em_comum_df is None:
        return pd.DataFrame(columns=["CNPJ", "Nome do fundo", "CDA_Protocolo", "CDA_Competencia", "CDA_Status"])

    # Cópia e padronização mínima
    rel = rel_em_comum_df.copy()

    # Garante as colunas que vamos criar, caso já existam mantém; caso contrário, cria depois do merge
    expected_cols = ["CDA_Protocolo", "CDA_Competencia", "CDA_Status"]

    # Se o DF do CDA veio vazio/None, apenas acrescenta colunas "Não possui"
    if df_cda is None or (isinstance(df_cda, pd.DataFrame) and df_cda.empty):
        for c in expected_cols:
            if c not in rel.columns:
                rel[c] = "Não possui"
        return rel

    # Normaliza chave de junção
    rel["CNPJ_Num"] = rel["CNPJ"].map(normaliza_cnpj)

    # Garante que df_cda tenha as colunas necessárias; se não tiver, cria vazias para não quebrar o merge
    df_cda = df_cda.copy()
    for c in ["CNPJ_Num", "CDA_Protocolo", "CDA_Competencia", "CDA_Status"]:
        if c not in df_cda.columns:
            df_cda[c] = None

    # Merge por CNPJ normalizado
    enx = rel.merge(
        df_cda[["CNPJ_Num", "CDA_Protocolo", "CDA_Competencia", "CDA_Status"]],
        on="CNPJ_Num", how="left"
    )

    # Preenche faltantes
    for c in expected_cols:
        enx[c] = enx[c].fillna("Não possui")
        
    # 🔽 PADRONIZA A COMPETÊNCIA DO CDA PARA 01/MM/AAAA
    if "CDA_Competencia" in enx.columns:
        enx["CDA_Competencia"] = enx["CDA_Competencia"].map(_competencia_to_01_mm_aaaa)


    # Posiciona colunas após "Mes de Referencia" (se existir)
    cols = list(enx.columns)
    insert_pos = cols.index("Mes de Referencia") + 1 if "Mes de Referencia" in cols else len(cols)
    for c in expected_cols:
        if c in cols:
            cols.remove(c)
    cols = cols[:insert_pos] + expected_cols + cols[insert_pos:]
    enx = enx[cols]

    # Remove coluna auxiliar
    if "CNPJ_Num" in enx.columns:
        enx = enx.drop(columns=["CNPJ_Num"])

    return enx  

# ======================= /CDA =====================================================

# ======================== Balancete ===============================================



def _linhas_excel_como_texto(arquivo_excel) -> list[str]:
    xls = pd.ExcelFile(arquivo_excel, engine="openpyxl")
    linhas = []
    for sheet in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet, dtype=str, header=None, engine="openpyxl")
        for _, row in df.iterrows():
            for val in row.tolist():
                s = str(val).strip() if pd.notna(val) else ""
                if s:
                    linhas.append(s)
    return [unicodedata.normalize("NFKD", s).strip() for s in linhas if s.strip()]

def _extrair_mm_yyyy_de_nome_arquivo(linhas: list[str]) -> Optional[str]:
    mm_yyyy = None
    for i, text in enumerate(linhas):
        if text.upper().startswith("NOME DO ARQUIVO"):
            for j in range(i+1, min(i+5, len(linhas))):
                cand = linhas[j]
                m = re.search(r"(\d{6})(?!\d)", cand)
                if m:
                    mm = m.group(1)[:2]
                    yyyy = m.group(1)[2:]
                    mm_yyyy = f"{mm}/{yyyy}"
                    return mm_yyyy
    return mm_yyyy

# --- Substitua sua parse_protocolo_balancete por esta (XLSX)
def parse_protocolo_balancete(arquivo_excel) -> pd.DataFrame:
    # Lê como texto cru
    df_raw = pd.read_excel(arquivo_excel, sheet_name=0, header=None, dtype=str, engine="openpyxl")

    # Achata as linhas não-vazias
    linhas = []
    for _, row in df_raw.iterrows():
        for val in row.values:
            if pd.isna(val):
                continue
            txt = str(val).strip()
            if txt:
                linhas.append(txt)


    pattern_cnpj = re.compile(r"(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})")
    pattern_mmYYYY6 = re.compile(r"(\d{6})(?!\d)")  # ex: 082025

    registros = []
    current = {"cnpj": None, "protocolo": None, "comp": None, "mmYYYY_file": None, "status": None}

    def flush():
        if current["cnpj"] and current["protocolo"]:
            cnpj_fmt = formatar_cnpj(current["cnpj"])
            comp = current["mmYYYY_file"] or current["comp"] or ""
            registros.append({
                "CNPJ": cnpj_fmt,
                "Balancete_Protocolo": current["protocolo"],
                "Balancete_Competencia": comp,
                "Balancete_Status": current.get("status") or ""
            })
        current.update({"cnpj": None, "protocolo": None, "comp": None, "mmYYYY_file": None, "status": None})

    i, n = 0, len(linhas)
    while i < n:
        up = linhas[i].upper()

        # Início novo bloco? fecha o anterior (se completo)
        if up.startswith("PROTOCOLO DE CONFIRMA"):
            if current["cnpj"] and current["protocolo"]:
                flush()
            i += 1
            continue

        # PARTICIPANTE -> CNPJ
        if up.startswith("PARTICIPANTE"):
            if current["cnpj"] and current["protocolo"]:
                flush()
            for j in range(i + 1, min(i + 12, n)):
                m = pattern_cnpj.search(linhas[j])
                if m:
                    current["cnpj"] = normaliza_cnpj(m.group(1))
                    break
            i += 1
            continue

        # NOME DO ARQUIVO -> captura 082025 => 08/2025 (agora olha até 4 linhas abaixo)
        if up.startswith("NOME DO ARQUIVO"):
            for j in range(i + 1, min(i + 5, n)):  # varre próximas linhas
                m = pattern_mmYYYY6.search(linhas[j])
                if m:
                    mm, yyyy = m.group(1)[:2], m.group(1)[2:]
                    current["mmYYYY_file"] = f"{mm}/{yyyy}"
                    break
            i += 1  # avança só uma posição; o while segue varrendo normalmente
            continue

        # COMPETÊNCIA
        if up.startswith("COMPET"):
            val = linhas[i + 1].strip() if (i + 1) < n else ""

            # 1) Tenta capturar MM/AAAA diretamente (padrão ideal)
            m2 = re.search(r"\b(\d{2})/(20\d{2})\b", val)
            if m2:
                current["comp"] = f"{m2.group(1)}/{m2.group(2)}"
            else:
                # 2) Tenta data com dia: aceitar MM/DD/AAAA ou DD/MM/AAAA
                m3 = re.search(r"\b(\d{2})/(\d{2})/(20\d{2})\b", val)
                if m3:
                    a, b, ano = int(m3.group(1)), int(m3.group(2)), int(m3.group(3))
                    # Heurística de plausibilidade:
                    if a > 12 and 1 <= b <= 12:
                        # DD/MM/AAAA -> usa MM = b
                        current["comp"] = f"{b:02d}/{ano}"
                    elif b > 12 and 1 <= a <= 12:
                        # MM/DD/AAAA -> usa MM = a
                        current["comp"] = f"{a:02d}/{ano}"
                    else:
                        # Ambíguo (ambos <= 12): preferir MM/DD/AAAA (observado nos protocolos)
                        current["comp"] = f"{a:02d}/{ano}"

            i += 2
            continue

        # 🔎 STATUS (NOVO): próximo valor após "Status:"
        if up.startswith("STATUS"):
            val = linhas[i + 1].strip() if (i + 1) < n else ""
            if val:
                current["status"] = val
            i += 2
            continue

        # Nº PROTOCOLO
        if (up.startswith("Nº PROTOCOLO") or up.startswith("N° PROTOCOLO") or
            up.startswith("NO PROTOCOLO") or up.startswith("Nº DO PROTOCOLO") or
            up.startswith("N° DO PROTOCOLO")):
            val = linhas[i + 1].strip() if (i + 1) < n else ""
            current["protocolo"] = val[:-2] if val.endswith(".0") else val
            i += 2
            continue

        i += 1

    # Flush final
    if current["cnpj"] and current["protocolo"]:
        flush()

    if not registros:
        return pd.DataFrame(columns=["CNPJ", "Balancete_Protocolo", "Balancete_Competencia", "Balancete_Status"])

    df = pd.DataFrame(registros).drop_duplicates("CNPJ", keep="first").reset_index(drop=True)
    return df

def parse_protocolo_balancete_from_pdf(uploaded_pdf) -> pd.DataFrame:
    text = _read_text_from_pdf(uploaded_pdf)
    if not text:
        return pd.DataFrame(columns=["CNPJ", "Balancete_Protocolo", "Balancete_Competencia", "Balancete_Status"])

    pattern_cnpj = re.compile(r"(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})")
    pattern_proto = re.compile(r"(?:N[º°]\s*PROTOCOLO|PROTOCOLO)\D*(\d{6,})", flags=re.I)
    pattern_comp_mm_yyyy = re.compile(r"(\b\d{2}/\d{4}\b)")
    pattern_status = re.compile(r"STATUS:\s*([\s\S]{0,40}?)(?:\r?\n|\r)", flags=re.I)  # captura linha após "Status:"

    registros = []
    for m in pattern_cnpj.finditer(text):
        cnpj_masked = m.group(1)
        start = m.start()

        window = text[start:start+600]
        proto_m = pattern_proto.search(window)
        protocolo = proto_m.group(1) if proto_m else ""

        prev_window = text[max(0, start-400):start+200]
        comp_m = pattern_comp_mm_yyyy.search(prev_window)
        competencia = comp_m.group(1) if comp_m else ""

        status_m = pattern_status.search(prev_window) or pattern_status.search(window)
        status_txt = status_m.group(1).strip() if status_m else ""

        cnpj_num = normaliza_cnpj(cnpj_masked)
        if cnpj_num:
            registros.append({
                "CNPJ": formatar_cnpj(cnpj_num),
                "Balancete_Protocolo": protocolo,
                "Balancete_Competencia": competencia,
                "Balancete_Status": status_txt
            })

    if not registros:
        return pd.DataFrame(columns=["CNPJ", "Balancete_Protocolo", "Balancete_Competencia", "Balancete_Status"])

    df = pd.DataFrame(registros).drop_duplicates(subset="CNPJ", keep="first").reset_index(drop=True)
    return df


# ========================== INTERFACE STREAMLIT ==========================
st.set_page_config(page_title="Batimento de Fundos - CadFi x Controle FIC",page_icon="banco_do_brasil_amarelo.ico", layout="centered")

st.title("Batimento de Fundos — Contabilidade FIC")
st.subheader("📊 1° - Batimento de Fundos — CadFi x Controle FIC")
st.caption("Interface web dos Batimentos. Faça o upload dos dois arquivos e clique em **Processar**.")

col1, col2 = st.columns(2)
with col1:
    cadfi_file = st.file_uploader("Arquivo CadFi (.xlsx)", type=["xlsx"], accept_multiple_files=False)
with col2:
    controle_file = st.file_uploader("Arquivo Controle FIC (.xlsx)", type=["xlsx", "xls"], accept_multiple_files=False)

processar = st.button("Processar", type="primary")

if processar:
    if not cadfi_file or not controle_file:
        st.error("⚠️ Envie os dois arquivos (CadFi e Controle Espelho) antes de processar.")
        st.stop()

    try:
        with st.spinner("Processando arquivos..."):
            cadfi_raw = carregar_excel(cadfi_file)                    # mantém como está (xlsx)
            cadfi_filtrado = filtrar_cadfi(cadfi_raw)

            controle_prep = carregar_controle_fic(controle_file)

            # APLICA FILTRO DE SIT A JAQUI (recomendado) — se a coluna não existir é noop
            controle_prep = filtrar_controle_por_situacao(controle_prep)

            # segue comparações com controle já restrito a SIT == 'A'
            df_fora = comparar_cnpjs(cadfi_filtrado, controle_prep)
            df_comum = comparar_fundos_em_comum(cadfi_filtrado, controle_prep)
            df_controle_fora = comparar_controle_fora_cadfi(cadfi_filtrado, controle_prep)

            df_controle_fora = filtrar_controle_por_situacao(df_controle_fora)
            df_controle_fora = filtrar_controle_por_nome(df_controle_fora)

            rel_fora = relatorio_fora_controle(df_fora)
            rel_comum = relatorio_em_comum(df_comum)
            rel_comum = remover_segundos_colunas(rel_comum, ["CDA_Protocolo", "CDA_Competencia"])
            rel_controle_fora = relatorio_controle_fora_cadfi(df_controle_fora)

            rel_comum = adicionar_drive_por_cnpj(rel_comum, controle_prep)
            
            if "COD GFI" in rel_comum.columns:
                for _df_name in ("rel_comum", "rel_fora", "rel_controle_fora"):
                    _df = locals()[_df_name]
                    if "COD GFI" in _df.columns:
                        cols = ["COD GFI"] + [c for c in _df.columns if c != "COD GFI"]
                        locals()[_df_name] = _df[cols]
            
            rel_fora = adicionar_drive_por_cnpj(rel_fora, controle_prep)
            rel_controle_fora = adicionar_drive_por_cnpj(rel_controle_fora, controle_prep)

            st.session_state["rel_comum"] = rel_comum
            st.session_state["rel_fora"] = rel_fora
            st.session_state["rel_controle_fora"] = rel_controle_fora

            # Salva mensagens fixas
            st.session_state["mensagens_batimento"] = [
                f"✅ Em comum: {len(rel_comum)} fundo(s)",
                f"ℹ️ No Controle e NÃO no CadFi: {len(rel_controle_fora)} fundo(s)",
                f"❌ Fora do Controle (presentes no CadFi, ausentes no Controle): {len(rel_fora)} fundo(s)"
            ]

            with st.expander("✅ Fundos presentes em AMBOS (CadFi e Controle)"):
                st.dataframe(rel_comum, use_container_width=True, hide_index=True)

            with st.expander("ℹ️ Fundos do Controle que NÃO estão no CadFi"):
                st.dataframe(rel_controle_fora, use_container_width=True, hide_index=True)

            with st.expander("❌ Fundos do CadFi que NÃO estão no Controle"):
                st.dataframe(rel_fora, use_container_width=True, hide_index=True)

            def gerar_zip_relatorios(rel_comum, rel_fora, rel_controle_fora):
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, "w") as zipf:
                    zipf.writestr("Relatorio_Fundos_Em_Ambos.xlsx", to_excel_bytes(rel_comum).getvalue())
                    zipf.writestr("Relatorio_Fundos_Somente_no_CadFi.xlsx", to_excel_bytes(rel_fora).getvalue())
                    zipf.writestr("Relatorio_Fundos_Somente_no_Controle.xlsx", to_excel_bytes(rel_controle_fora).getvalue())
                zip_buffer.seek(0)
                return zip_buffer

            st.download_button(
                label="⬇️ Baixar TODOS os relatórios (.zip)",
                data=gerar_zip_relatorios(rel_comum, rel_fora, rel_controle_fora),
                file_name="Relatorios_Batimento_CadFi_Controle.zip",
                mime="application/zip"
            )

    except Exception as e:
        st.error("❌ Erro ao processar os arquivos.")
        st.exception(e)

# Exibe mensagens fixas fora do bloco de processamento
if "mensagens_batimento" in st.session_state:
    for msg in st.session_state["mensagens_batimento"]:
        st.markdown(msg)



# ========================== INTERFACE: CDA (Enriquecer "Em Ambos") ==========================
st.markdown("---")
st.subheader("📄 2° CDA — Enriquecer o relatório **Fundos em Ambos** com Protocolo/Competência")

col_cda1, col_cda2 = st.columns(2)
with col_cda1:
    rel_ambos_file = st.file_uploader("Relatório — Fundos em Ambos (xlsx)", type=["xlsx"], key="rel_ambos_cda")
with col_cda2:
    cda_proto_file = st.file_uploader("Planilha de Protocolo do CDA (xlsx)", type=["xlsx"], key="cda_proto_file")

bt_cda = st.button("Preencher colunas do CDA", type="primary", key="btn_cda_process")

if bt_cda:
    if not rel_ambos_file or not cda_proto_file:
        st.error("⚠️ Envie **os dois arquivos**: (1) Relatório 'Em Ambos' e (2) Protocolo do CDA.")
        st.stop()
    try:
        with st.spinner("Lendo arquivos e integrando CDA..."):
            df_ambos = pd.read_excel(rel_ambos_file, dtype=str)
            df_ambos = padronizar_colunas(df_ambos)

            if "CNPJ" not in df_ambos.columns:
                st.error("O relatório 'Em Ambos' precisa ter a coluna 'CNPJ'.")
                st.stop()

            df_cda = parse_protocolos_cda_xlsx(cda_proto_file)
            df_final = enriquecer_em_comum_com_cda(df_ambos, df_cda)

            tot = len(df_final)
            casados = df_final["CDA_Protocolo"].astype(str).str.strip().ne("Não possui").sum()
            st.success(f"✅ Encontramos protocolo do CDA para {casados} de {tot} fundos.")
            
            st.session_state["mensagens_cda"] = [
                f"✅ Encontramos protocolo do CDA para {casados} de {tot} fundos."
            ]


            with st.expander("🔎 Prévia do Batimento do CDA"):
                st.dataframe(df_final, use_container_width=True, hide_index=True)

            st.download_button(
                label="⬇️ Baixar — Batimento do CDA",
                data=to_excel_bytes(df_final, sheet_name="Em_Ambos_com_CDA"),
                file_name="Batimento do CDA.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            
    except Exception as e:
        st.exception(e)
        
if "mensagens_cda" in st.session_state:
    for msg in st.session_state["mensagens_cda"]:
        st.markdown(msg)


# ============================== Interface de Balancete ==============================
st.markdown("## 🔄 3º - Enriquecer batimento com Balancete")

colb1, colb2 = st.columns(2)
with colb1:
    relatorio_ambos_file = st.file_uploader(
        "Arquivo Relatório de Ambos com CDA (.xlsx)",
        type=["xlsx"],
        key="relatorio_ambos"
    )
with colb2:
    balancete_file = st.file_uploader(
        "Arquivo de Balancete (XLSX ou PDF)",
        type=["xlsx", "pdf"],
        accept_multiple_files=False
    )

enriquecer = st.button("Preencher colunas Balancete", type="primary", key="btn_balancete_enriquecer")

if enriquecer:
    if not relatorio_ambos_file or not balancete_file:
        st.error("⚠️ Envie os dois arquivos antes de enriquecer.")
        st.stop()

    try:
        with st.spinner("Enriquecendo com Balancete..."):
            # 1) Carrega relatório 'Em Ambos'
            df_rel_comum = pd.read_excel(relatorio_ambos_file, dtype=str)
            df_rel_comum = padronizar_colunas(df_rel_comum)

            if "CNPJ" not in df_rel_comum.columns:
                st.error("O relatório 'Em Ambos' precisa ter a coluna 'CNPJ'.")
                st.stop()

            # 2) Parse do arquivo de balancete (xlsx mais confiável; pdf heurístico)
            fname = str(getattr(balancete_file, "name", "")).lower()
            if fname.endswith(".xlsx"):
                df_balancete_proto = parse_protocolo_balancete(balancete_file)
            else:
                df_balancete_proto = parse_protocolo_balancete_from_pdf(balancete_file)

            # 3) Padroniza colunas e normaliza CNPJ nas duas pontas
            df_balancete_proto = padronizar_colunas(df_balancete_proto)

            # Normaliza CNPJ do relatório-base
            df_rel_comum["CNPJ"] = df_rel_comum["CNPJ"].apply(
                lambda x: formatar_cnpj(normaliza_cnpj(x)) if pd.notna(x) else None
            )

            # Normaliza CNPJ do balancete (se existir)
            if "CNPJ" in df_balancete_proto.columns:
                df_balancete_proto["CNPJ"] = df_balancete_proto["CNPJ"].apply(
                    lambda x: formatar_cnpj(normaliza_cnpj(x)) if pd.notna(x) else None
                )
            else:
                st.warning(
                    "Não foi possível extrair CNPJ do arquivo de Balancete — verifique o layout. "
                    "O resultado pode ficar vazio."
                )

            # 4) Fallback: garante as colunas esperadas do balancete para o merge
            for c in ["Balancete_Protocolo", "Balancete_Competencia", "Balancete_Status"]:
                if c not in df_balancete_proto.columns:
                    df_balancete_proto[c] = None

            # 5) Merge por CNPJ
            merged = df_rel_comum.merge(
                df_balancete_proto[["CNPJ", "Balancete_Protocolo", "Balancete_Competencia", "Balancete_Status"]],
                on="CNPJ",
                how="left"
            )

            # Preenche vazios
            for c in ["Balancete_Protocolo", "Balancete_Competencia", "Balancete_Status"]:
                merged[c] = merged[c].fillna("Não possui")

            # 🔽 PADRONIZA COMPETÊNCIA para 01/MM/AAAA (CDA e Balancete):
            for col in ["CDA_Competencia", "Balancete_Competencia"]:
                if col in merged.columns:
                    merged[col] = merged[col].map(_competencia_to_01_mm_aaaa)


            cols = list(merged.columns)
            insert_pos = cols.index("Mes de Referencia") + 1 if "Mes de Referencia" in cols else len(cols)

            for c in ["Balancete_Protocolo", "Balancete_Competencia", "Balancete_Status"]:
                if c in cols:
                    cols.remove(c)

            cols = (
                cols[:insert_pos]
                + ["Balancete_Protocolo", "Balancete_Competencia", "Balancete_Status"]
                + cols[insert_pos:]
            )
            merged = merged[cols]

            # 7) Exibe e disponibiliza download
            encontrados = merged["Balancete_Protocolo"].astype(str).str.strip().ne("Não possui").sum()
            st.success(f"✅ Enriquecido com {encontrados} protocolos encontrados.")
            st.dataframe(merged, use_container_width=True, hide_index=True)

            # Mensagem fixa + download
            st.session_state["mensagens_balancete"] = [
                f"✅ Enriquecido com {encontrados} protocolos encontrados."
            ]
            st.download_button(
                label="⬇️ Baixar — Batimento do CDA e do Balancete",
                data=to_excel_bytes(merged, sheet_name="Batimento do CDA e do Balancete"),
                file_name="Batimento do CDA e do Balancete.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        # ... após montar `merged`
        st.session_state["rel_enriquecido_balancete"] = merged  # <- adiciona esta linha


    except Exception as e:
        st.exception(e)

# Mensagens persistentes
if "mensagens_balancete" in st.session_state:
    for msg in st.session_state["mensagens_balancete"]:
        st.markdown(msg)

# ============================== 4º - Validação de Competência (CDA & Balancete) ==============================
st.markdown("## ✅ 4º - Validação de Competência (CDA & Balancete)")

with st.form("form_validacao_comp"):
    modo = st.radio("Validar por:", ("Data exata (DD/MM/AAAA)", "Mês/Ano (MM/AAAA)"), horizontal=True)
    if modo.startswith("Data exata"):
        data_alvo = st.text_input("Data da competência (DD/MM/AAAA)", value="01/08/2025", placeholder="DD/MM/AAAA")
        mes_ano_alvo = None
    else:
        mes_ano_alvo = st.text_input("Mês/Ano da competência (MM/AAAA)", value="08/2025", placeholder="MM/AAAA")
        data_alvo = None

    contar_nao_possui = st.checkbox('Contar "Não possui" como erro', value=True)
    validar_btn = st.form_submit_button("Validar agora")

if validar_btn:
    df_base = st.session_state.get("rel_enriquecido_balancete")  # gerado no passo 3
    if df_base is None:
        st.warning("Antes, rode o 3º passo (Balancete) para gerar o relatório enriquecido.")
    else:
        try:
            if modo.startswith("Data exata"):
                inconsist = validar_por_data_exata(df_base, data_alvo, contar_nao_possui=contar_nao_possui)
                titulo_rel = f"Divergencias_Competencia_{data_alvo.replace('/', '-')}"
                alvo_msg = data_alvo
            else:
                inconsist = validar_por_mes_ano(df_base, mes_ano_alvo, contar_nao_possui=contar_nao_possui)
                titulo_rel = f"Divergencias_Competencia_{mes_ano_alvo.replace('/', '-')}"
                alvo_msg = mes_ano_alvo
        except ValueError as e:
            st.error(str(e))
            st.stop()

        # Resumo correto (CNPJ únicos)
        resumo = resumo_divergencias(inconsist, df_base)
        if resumo["fundos_com_erro"] == 0:
            st.success(f"Tudo certo! Nenhuma divergência para {alvo_msg}. "
                       f"Fundos na base: {resumo['total_fundos']}.")
        else:
            perc = (resumo["fundos_com_erro"]/resumo["total_fundos"]) if resumo["total_fundos"] else 0
            st.error(
                f"Foram encontrados **{resumo['fundos_com_erro']} fundos** com divergência "
                f"({perc:.1%} do total de {resumo['total_fundos']}).\n\n"
                f"Linhas de divergência: {resumo['linhas']}."
            )
            st.caption(
                f"Quebra por origem — Somente **CDA**: {resumo['somente_cda']} • "
                f"Somente **Balancete**: {resumo['somente_balancete']} • "
                f"**Ambos**: {resumo['ambos']}"
            )

            # 1) Grid de linhas (auditoria)
            with st.expander("🔎 Ver linhas de divergência (CDA e Balancete)"):
                st.dataframe(inconsist, use_container_width=True, hide_index=True)
                st.download_button(
                    "⬇️ Baixar (linhas) — Divergências por origem",
                    data=to_excel_bytes(inconsist, sheet_name="Divergencias_Linhas"),
                    file_name=f"{titulo_rel}_linhas.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

            # 2) Consolidado por fundo (uma linha por CNPJ)
            consol = consolidar_incons_por_fundo(inconsist)

            # Segmentos por CNPJ
            tem_cda = consol["CDA atual"].notna() | consol["CDA esperada"].notna()
            tem_bal = consol["Balancete atual"].notna() | consol["Balancete esperada"].notna()
            df_so_cda = consol[tem_cda & ~tem_bal]
            df_so_bal = consol[~tem_cda & tem_bal]
            df_ambos  = consol[tem_cda & tem_bal]

            with st.expander("🧮 Consolidado por fundo (1 linha por CNPJ)"):
                st.dataframe(consol, use_container_width=True, hide_index=True)
                st.download_button(
                    "⬇️ Baixar (fundos) — Consolidado geral",
                    data=to_excel_bytes(consol, sheet_name="Consolidado_Fundos"),
                    file_name=f"{titulo_rel}_fundos.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

            col_a, col_b, col_c = st.columns(3)
            with col_a:
                st.write(f"**Somente CDA** ({len(df_so_cda)} fundos)")
                st.dataframe(df_so_cda, use_container_width=True, hide_index=True)
                st.download_button(
                    "⬇️ Baixar — Somente CDA",
                    data=to_excel_bytes(df_so_cda, sheet_name="Somente_CDA"),
                    file_name=f"{titulo_rel}_somente_CDA.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            with col_b:
                st.write(f"**Somente Balancete** ({len(df_so_bal)} fundos)")
                st.dataframe(df_so_bal, use_container_width=True, hide_index=True)
                st.download_button(
                    "⬇️ Baixar — Somente Balancete",
                    data=to_excel_bytes(df_so_bal, sheet_name="Somente_Balancete"),
                    file_name=f"{titulo_rel}_somente_Balancete.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            with col_c:
                st.write(f"**Ambos** ({len(df_ambos)} fundos)")
                st.dataframe(df_ambos, use_container_width=True, hide_index=True)
                st.download_button(
                    "⬇️ Baixar — Ambos",
                    data=to_excel_bytes(df_ambos, sheet_name="Ambos"),
                    file_name=f"{titulo_rel}_ambos.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
