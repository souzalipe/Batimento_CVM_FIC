            controle_prep = carregar_controle_fic(controle_file)

            # APLICA FILTRO DE SIT A JAQUI (recomendado) — se a coluna não existir é noop
            controle_prep = filtrar_controle_por_situacao(controle_prep)

            # segue comparações com controle já restrito a SIT == 'A'
            df_fora = comparar_cnpjs(cadfi_filtrado, controle_prep)
            df_comum = comparar_fundos_em_comum(cadfi_filtrado, controle_prep)
            df_controle_fora = comparar_controle_fora_cadfi(cadfi_filtrado, controle_prep)
