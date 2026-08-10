# -*- coding: utf-8 -*-
"""
Extrator de bolões via API (interceptação JSON) — Caixa.

Fluxo [1] AUTOMÁTICO (principal):
  1. Terminal pede: MODALIDADE + CONCURSO (antes de abrir o Edge)
  2. Edge abre — faça LOGIN → escolha SÓ a modalidade → PAUSA → digite SIM
  3. Script clica Detalhes e avança páginas até acabar (sem filtro de estado)
  4. JSON gravado em json-boloes/ em tempo real

Fluxo [2] MANUAL (opcional): ENTER a cada página / vários filtros na mesma sessão.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Optional, Tuple

from selenium import webdriver

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from boloes_api_caixa import (
    LEGENDA_API,
    aguardar_capturas_api,
    aguardar_detalhes_visiveis,
    contar_respostas_detalhar,
    detalhar_pagina_ate_esperado,
    detectar_detalhes_pagina,
    instalar_interceptador_api,
    ler_capturas_api,
    ler_metadados_paginacao_api,
    limpar_capturas_api,
    limpar_marcas_detalhes_pagina,
    preparar_pagina_para_detalhes,
    resumo_capturas,
    salvar_capturas_brutas,
)
from boloes_modalidades import (
    TECLAS_ESPECIAIS,
    TODAS_MODALIDADES,
    extrair_concurso_de_boloes,
    extrair_modalidade_de_boloes,
    imprimir_menu_modalidades,
    nome_arquivo_consolidado_padrao,
    nome_arquivo_sessao,
    resolver_modalidade_menu,
)
from boloes_consolidar import (
    carregar_json_boloes,
    consolidar_sessao,
    hashes_de_lista,
    hashes_pagina,
    localizar_arquivo_sessao_existente,
    mesclar_listas,
    salvar_json_boloes,
    salvar_json_continuacao,
)
from boloes_checkpoint import (
    STATUS_CONCLUIDO,
    STATUS_EXECUTANDO,
    STATUS_PAUSADO,
    carregar_checkpoint,
    instruir_pause,
    pause_solicitada,
    perguntar_retomada,
    reset_pause_flags,
    salvar_checkpoint,
)
from boloes_estados import estados_varredura, imprimir_fila_estados
from boloes_pasta_bds import detectar_modalidade_site
from boloes_filtro_loterica import (
    FiltroLotericaConfig,
    _carregar_config_cache,
    aplicar_filtro_varredura_automatica,
    bolao_atende_filtro,
    bolao_corresponde_loterica,
    cfg_qualquer_loterica,
    eh_ultima_pagina,
    gerar_arquivo_base,
    garantir_sessao_caixa,
    ler_config_extracao,
    ler_filtro_aplicado_site,
    parse_termo_loterica,
    aplicar_filtro_loterica,
    ir_proxima_pagina_lista,
    ir_para_pagina_lista,
    ir_direto_para_pagina_lista,
    preparar_pagina_loterica,
    sessao_caixa_ativa,
    slug_loterica,
    ultima_pagina_detectada,
)

CONFERENCIAS_BOLOES_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, '..'))
PASTA_JSON = os.path.join(CONFERENCIAS_BOLOES_DIR, 'json-boloes')
PASTA_CAPTURAS = os.path.join(CONFERENCIAS_BOLOES_DIR, 'capturas-api')
URL_BOLOES = 'https://www.loteriasonline.caixa.gov.br/silce-web/#/bolao-caixa'

MSG_ULTIMA_PAGINA = 'Última página — botão Seguinte desabilitado. Extração concluída.'

VERSAO_EXTRATOR = 'CHECKPOINT-RESUME-v3.3 (lista do site — sem filtro UF)'


for _pasta in (CONFERENCIAS_BOLOES_DIR, PASTA_JSON, PASTA_CAPTURAS):
    os.makedirs(_pasta, exist_ok=True)

driver = None
FILTRO_LOTERICA: Optional[FiltroLotericaConfig] = None
ROTULO_ARQUIVO = None
ROTULO_NOME = 'modalidade atual'
SESSAO_AUTORIZADA = False


def _login_auto_habilitado() -> bool:
    """True se o .bat (LOGIN_CAIXA_AUTO) ou config.local.json pedir login automático."""
    flag = (os.environ.get('LOGIN_CAIXA_AUTO') or '').strip().lower()
    if flag in ('1', 'true', 'sim', 'yes', 'on'):
        return True
    if flag in ('0', 'false', 'nao', 'não', 'no', 'off'):
        return False
    cfg = os.path.join(CONFERENCIAS_BOLOES_DIR, 'config.local.json')
    if not os.path.isfile(cfg):
        return False
    try:
        with open(cfg, encoding='utf-8') as f:
            dados = json.load(f)
        return bool(dados.get('login_automatico'))
    except Exception:
        return False


def _aviso_sonoro_extracao_completa() -> None:
    """Beep quando a lista do site foi paginada até o fim."""
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
        for freq, dur in ((784, 180), (988, 180), (1175, 350)):
            winsound.Beep(freq, dur)
    except Exception:
        try:
            print('\a\a\a', end='', flush=True)
        except Exception:
            pass
    _out('\n  [SOM] Extração completa — todas as páginas processadas.')


# ─────────────────────────────────────────────────────────────────────────────
# NOVO: coleta de modalidade + concurso ANTES de abrir o Edge
# ─────────────────────────────────────────────────────────────────────────────

MAPA_MODALIDADES_RAPIDO = {
    '1': 'MEGA_SENA',
    '2': 'QUINA',
    '3': 'LOTOFACIL',
    '4': 'LOTOMANIA',
    '5': 'TIMEMANIA',
    '6': 'DIA_DE_SORTE',
    '7': 'SUPER_SETE',
    '8': 'DUPLA_SENA',
    '9': 'MAIS_MILIONARIA',
}

    # Especiais: usa diretamente o TECLAS_ESPECIAIS já definido no seu código


def _separador(char='=', n=60):
    print(char * n, flush=True)


def _out(msg: str = '') -> None:
    """Print imediato no terminal."""
    print(msg, flush=True)


def _coletar_modalidade_pre_extracao() -> Optional[object]:
    """
    Pergunta a modalidade ANTES de abrir o Edge.
    Retorna objeto de modalidade ou None (auto-detectar depois).
    """
    _separador()
    _out('  PASSO 1 — MODALIDADE')
    _out('  Informe a modalidade que você vai extrair no site:')
    _out('')
    _out('  [1] Mega-Sena       [2] Quina         [3] Lotofácil')
    _out('  [4] Lotomania       [5] Timemania      [6] Dia de Sorte')
    _out('  [7] Super Sete      [8] Dupla Sena     [9] +Milionária')
    _out('  Especiais: QSJ | DSP | LTI | MSV | MS3')
    _out('  ENTER = detectar automaticamente no site')
    _separador('-')

    try:
        resp = input('  Modalidade: ').strip().upper()
    except EOFError:
        return None

    if not resp:
        _out('  [OK] Modalidade será detectada automaticamente ao iniciar.')
        return None

    # Número 1-9
    if resp in MAPA_MODALIDADES_RAPIDO:
        slug = MAPA_MODALIDADES_RAPIDO[resp]
        mod = resolver_modalidade_menu(slug)
        if mod:
            _out(f'  [OK] Modalidade: {mod.label}')
            return mod

    # Especiais: QSJ, DSP, LTI, etc. — usa o mesmo TECLAS_ESPECIAIS do seu código
    if resp in TECLAS_ESPECIAIS:
        mod = resolver_modalidade_menu(resp)
        if mod:
            _out(f'  [OK] Modalidade especial: {mod.label}')
            return mod

    # Texto livre (MEGA_SENA, QUINA, etc.)
    mod = resolver_modalidade_menu(resp)
    if mod:
        _out(f'  [OK] Modalidade: {mod.label}')
        return mod

    _out(f'  [AVISO] "{resp}" não reconhecido — será detectado automaticamente.')
    return None


def _coletar_concurso_pre_extracao() -> str:
    """
    Pergunta o número do concurso ANTES de abrir o Edge.
    Retorna string com dígitos ou '' para auto-detectar.
    """
    _separador()
    _out('  PASSO 2 — CONCURSO')
    _out('  Informe o número do concurso que você vai extrair:')
    _out('  Exemplo: 3024  |  ENTER = detectar automaticamente no site')
    _separador('-')

    try:
        resp = input('  Concurso nº: ').strip()
    except EOFError:
        return ''

    digits = re.sub(r'\D', '', resp)
    if digits:
        _out(f'  [OK] Concurso informado: {digits}')
        return digits

    _out('  [OK] Concurso será detectado automaticamente da primeira página.')
    return ''


def _exibir_resumo_pre_extracao(mod, concurso: str, cfg) -> None:
    """Mostra no terminal o resumo completo antes de abrir o Edge."""
    _separador()
    _out('  RESUMO — CONFIGURAÇÃO DA EXTRAÇÃO')
    _separador('-')
    _out(f'  Modalidade  : {mod.label if mod else "detectar automaticamente"}')
    _out(f'  Concurso    : {concurso if concurso else "detectar automaticamente"}')
    _out('  Lotérica    : qualquer (lista do site — SEM filtro de estado)')
    _out(f'  Destino     : json-boloes/')
    _out(f'  Gravação    : tempo real (KBs crescem a cada página)')
    _separador()
    _out('')
    _out('  Agora:')
    _out('  1. O Edge abre e o LOGIN é automático')
    _out('  2. No Edge: escolha SÓ a MODALIDADE (não mexa em estado/lotérica)')
    _out('  3. PAUSA — volte aqui e digite SIM (Enter vazio NÃO inicia)')
    _out('  4. O script clica Detalhes e avança páginas até acabar')
    _separador()


# ─────────────────────────────────────────────────────────────────────────────
# Utilitários gerais
# ─────────────────────────────────────────────────────────────────────────────

def _driver_url(timeout: float = 6.0) -> str:
    if driver is None:
        return ''

    def _ler() -> str:
        try:
            return (driver.execute_script('return window.location.href || "";') or '').strip()
        except Exception:
            return (driver.current_url or '').strip()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_ler).result(timeout=timeout)
    except FuturesTimeout:
        _out('  [AVISO] Edge não respondeu a tempo — clique na janela do navegador.')
        return ''
    except Exception:
        return ''


def _no_site_boloes() -> bool:
    url = _driver_url().lower()
    if not url:
        return driver is not None and sessao_caixa_ativa(driver)
    if any(x in url for x in ('login.caixa.gov.br', 'openid-connect', '/auth/realms/')):
        return False
    return 'loteriasonline.caixa.gov.br' in url or 'silce-web' in url


def _novo_painel_extracao() -> dict:
    return {
        'paginas_processadas': 0,
        'paginas_com_dados': 0,
        'paginas_vazias': 0,
        'capturas_api': 0,
        'descartados_loterica': 0,
        'por_pagina': {},
    }


def _imprimir_painel_pagina(pagina: int, n_novos: int, boloes: list, hashes: set, painel: dict) -> None:
    painel['paginas_processadas'] = pagina
    painel['por_pagina'][pagina] = n_novos
    if n_novos > 0:
        painel['paginas_com_dados'] += 1
    else:
        painel['paginas_vazias'] += 1

    pag_com = painel['paginas_com_dados']
    pag_vaz = painel['paginas_vazias']
    caps_pag = painel.get('capturas_ultima_pagina', 0)
    n_det = painel.get('detalhes_tela_pagina', 0)
    pend = painel.get('pendentes_pagina', 0)

    # Mostra tamanho do arquivo JSON em tempo real
    ab = painel.get('arquivo_base', '')
    kb_info = ''
    if ab:
        path_json = os.path.join(PASTA_JSON, f'{ab}.json')
        if os.path.isfile(path_json):
            kb = os.path.getsize(path_json) / 1024
            kb_info = f' | 💾 {kb:.1f} KB no disco'

    print('\n  ' + '-' * 56)
    print(f'  [PAINEL] Página {pagina} concluída{kb_info}')
    linha = f'    Nesta página : +{n_novos} registro(s) | {caps_pag} captura(s) API'
    if n_det:
        linha += f' | detalhes_tela={n_det}'
        if pend:
            linha += f' | faltam={pend}'
    print(linha)
    print(f'    Total sessão  : {len(boloes)} registro(s) | {len(hashes)} único(s)')
    print(f'    Páginas       : {pagina} processada(s) | {pag_com} com dados | {pag_vaz} vazia(s)')
    if painel['capturas_api']:
        print(f'    Capturas API  : {painel["capturas_api"]} acumulada(s) na sessão')
    if painel['descartados_loterica']:
        print(f'    Descartados   : {painel["descartados_loterica"]} (outra lotérica)')
    print('  ' + '-' * 56)


def _imprimir_resumo_final(
    boloes: list,
    hashes: set,
    painel: dict,
    arquivo_base: str,
    cfg,
    tempo_seg: int,
) -> None:
    path_sessao = os.path.join(PASTA_JSON, f'{arquivo_base}.json')
    kb_final = os.path.getsize(path_sessao) / 1024 if os.path.isfile(path_sessao) else 0

    print('\n' + '=' * 60)
    print('  RESUMO FINAL DA EXTRAÇÃO')
    print('=' * 60)
    print(f'\n  Lotérica alvo      : {cfg.termo or ("QUALQUER" if cfg.qualquer_loterica else "(filtro manual)")}')
    print(f'  Páginas processadas: {painel["paginas_processadas"]}')
    print(f'  Páginas com dados  : {painel["paginas_com_dados"]}')
    print(f'  Páginas vazias     : {painel["paginas_vazias"]}')
    print(f'  Registros no arquivo : {len(boloes)} (modalidade + concurso — pronto p/ importar)')
    if painel.get('registros_loterica_alvo') is not None and cfg.termo:
        print(f'  Lotérica alvo (ref.) : {painel["registros_loterica_alvo"]} reg.')
    print(f'  Hashes únicos sessão : {len(hashes)}')
    cont = painel.get('continuidade')
    if cont:
        print(f'  Base preservada    : {cont["existentes"]} reg. em {cont["arquivo"]} ({cont.get("kb", "?")} KB)')
    if os.path.isfile(path_sessao):
        total_disco = len(carregar_json_boloes(path_sessao))
        novos_sessao = max(0, total_disco - (cont['existentes'] if cont else 0))
        print(f'  Total no arquivo   : {total_disco} reg. (+{novos_sessao} novo(s) nesta extração)')
    print(f'  Capturas API       : {painel["capturas_api"]} JSON(s)')
    if painel['descartados_loterica']:
        print(f'  Descartados        : {painel["descartados_loterica"]} (lotérica diferente)')
    if painel.get('descartados_modalidade'):
        print(f'  Descartados        : {painel["descartados_modalidade"]} (modalidade diferente)')
    print(f'  Tempo              : {tempo_seg // 60}min {tempo_seg % 60}s')
    print(f'  Arquivo            : {path_sessao}')
    print(f'  Tamanho final      : {kb_final:.1f} KB')

    if painel['por_pagina']:
        print('\n  Registros por página:')
        for pg in sorted(painel['por_pagina']):
            n = painel['por_pagina'][pg]
            barra = '#' * min(n, 40) if n else '(vazia)'
            print(f'    Pág {pg:>3}: {n:>4}  {barra}')
    print('=' * 60)


def _rotulo_nome() -> str:
    return ROTULO_ARQUIVO.label if ROTULO_ARQUIVO else 'modalidade atual'


def _rotulo_modalidade_menu() -> str:
    if not ROTULO_ARQUIVO:
        return '(não configurada)'
    m = ROTULO_ARQUIVO
    if getattr(m, 'especial', False) and m.tecla:
        return f'{m.tecla} — {m.label}'
    num = getattr(m, 'numero', None)
    if num and num <= 9:
        return f'[{num}] {m.label}'
    return m.label


def _imprimir_tabela_modalidades_resumida() -> None:
    _out('\n  OPCIONAL — forçar parser no terminal (senão usa API do site):')
    _out('  M1 Mega-Sena   M2 Quina        M3 Lotofácil')
    _out('  M4 Lotomania   M5 Timemania    M6 Dia de Sorte')
    _out('  M7 Super Sete  M8 Dupla Sena   M9 +Milionária')
    _out('  Especiais: DSP | QSJ | LTI | MSV | MS3')


def _imprimir_status_modalidade() -> None:
    if ROTULO_ARQUIVO:
        _out(f'\n  Parser terminal (opcional): {_rotulo_modalidade_menu()}')
    else:
        _out('\n  Modalidade: vem da API do site (MEGA_SENA, QUINA…) — não precisa M1.')


def _aplicar_modalidade(mod) -> bool:
    global ROTULO_ARQUIVO, ROTULO_NOME
    if not mod:
        return False
    ROTULO_ARQUIVO = mod
    ROTULO_NOME = _rotulo_nome()
    _out(f'\n>>> Modalidade: {_rotulo_modalidade_menu()}')
    if getattr(mod, 'especial', False):
        _out(f'>>> Base: {mod.base_label} | Época: {mod.epoca}')
    _out(f'>>> Extrai: {mod.extracao}')
    return True


def _trocar_modalidade_por_entrada(entrada: str) -> bool:
    mod = resolver_modalidade_menu(entrada)
    if not mod:
        return False
    return _aplicar_modalidade(mod)


# ─────────────────────────────────────────────────────────────────────────────
# Navegador
# ─────────────────────────────────────────────────────────────────────────────

def iniciar_navegador() -> bool:
    global driver
    if driver is not None:
        return True
    try:
        _out('\nIniciando Edge (hook API)...')
        opts = webdriver.EdgeOptions()
        opts.page_load_strategy = 'eager'
        opts.add_experimental_option('detach', True)
        driver = webdriver.Edge(options=opts)
        driver.set_page_load_timeout(45)
        instalar_interceptador_api(driver)

        # Login automatizado opcional (ativado pelo .bat via LOGIN_CAIXA_AUTO=1
        # ou por "login_automatico": true em config.local.json).
        # Não altera o restante do extrator — só antecipa a autenticação.
        if _login_auto_habilitado():
            _out('\n  [LOGIN AUTO] Fluxo de autenticação Caixa (módulo isolado)...')
            try:
                from login_caixa.fluxo import (
                    LoginAutomatizadoError,
                    executar_login_automatizado,
                    _parece_logado_no_portal,
                )
                executar_login_automatizado(driver=driver, manter_navegador_aberto=True)
                _out('  [LOGIN AUTO] Login concluído.')
            except LoginAutomatizadoError as exc:
                _out(f'  [LOGIN AUTO] Interrompido: {exc}')
                _out('  >>> Continue o login MANUALMENTE no Edge.')
            except Exception as exc:
                _out(f'  [LOGIN AUTO] Falha inesperada: {exc}')
                _out('  >>> Continue o login MANUALMENTE no Edge.')

            # Após login: só garante estar no portal logado.
            # Modalidade/filtros = MANUAL no Edge. Extração só depois do SIM.
            try:
                ja_logado = _parece_logado_no_portal(driver)
            except Exception:
                ja_logado = False
            try:
                url_atual = (driver.current_url or '').lower()
            except Exception:
                url_atual = ''

            if ja_logado and 'bolao-caixa' not in url_atual:
                _out('  Abrindo Bolões Caixa (você já está logado)...')
                try:
                    driver.get(URL_BOLOES)
                except Exception as exc:
                    _out(f'  [AVISO] Não abriu bolões automaticamente: {exc}')
            elif not ja_logado:
                _out('  [AVISO] Sessão ainda não confirmada — abra Bolões no Edge se precisar.')
                try:
                    driver.get(URL_BOLOES)
                except Exception:
                    pass
        else:
            driver.get(URL_BOLOES)

        _out('')
        _out('  ' + '=' * 56)
        _out('  LOGIN AUTO FINALIZADO — agora é com VOCÊ no Edge:')
        _out('    1) Confirme que está LOGADO')
        _out('    2) Escolha SÓ a MODALIDADE no site (não mexa em estado)')
        _out('    3) Volte ao terminal e digite SIM para iniciar a extração')
        _out('  ' + '=' * 56)
        return True
    except Exception as exc:
        print(f'\n>>> ERRO ao abrir Edge: {exc}')
        traceback.print_exc()
        driver = None
        return False


def _aguardar_login_inicial() -> bool:
    """Legado — não usar no [1]; preferir ENTER manual em aguardar_site_pronto()."""
    fim = time.time() + 180
    while time.time() < fim:
        try:
            if _usuario_logado_caixa() or _no_site_boloes():
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def fechar_navegador() -> None:
    global driver
    if driver is not None:
        try:
            print('\nFechando navegador...')
            driver.quit()
        except Exception:
            pass
        driver = None


# ─────────────────────────────────────────────────────────────────────────────
# Configuração de lotérica e modalidade
# ─────────────────────────────────────────────────────────────────────────────

def configurar_modalidade_apenas() -> bool:
    global ROTULO_ARQUIVO, ROTULO_NOME
    try:
        from boloes_modalidades import ler_modalidade_terminal
        ROTULO_ARQUIVO = ler_modalidade_terminal()
        ROTULO_NOME = _rotulo_nome()
        print(f'\n>>> Modalidade: {ROTULO_NOME}')
        print('>>> Modo [2]: lotérica e dezenas você escolhe NO SITE a cada rodada.')
        return True
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        print(f'\n>>> ERRO na modalidade: {exc}')
        return False


def configurar_loterica() -> bool:
    global FILTRO_LOTERICA, ROTULO_ARQUIVO, ROTULO_NOME
    try:
        FILTRO_LOTERICA, ROTULO_ARQUIVO = ler_config_extracao()
        ROTULO_NOME = _rotulo_nome()
        if not FILTRO_LOTERICA or not (FILTRO_LOTERICA.termo or '').strip():
            print('\n>>> Lotérica inválida ou vazia. Tente de novo (ex.: 9833).')
            FILTRO_LOTERICA = None
            return False
        print(f'\n>>> Config OK | Lotérica: {FILTRO_LOTERICA.termo} | Modalidade: {ROTULO_NOME}')
        return True
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        print(f'\n>>> ERRO na configuração: {exc}')
        traceback.print_exc()
        return False


def _exigir_config_extracao(acao: str = 'extrair') -> bool:
    if FILTRO_LOTERICA and (
        (FILTRO_LOTERICA.termo or '').strip()
        or FILTRO_LOTERICA.codigo
        or FILTRO_LOTERICA.qualquer_loterica
    ):
        return True

    print('\n' + '=' * 60)
    print('  FILTRO NÃO CONFIGURADO')
    print('=' * 60)
    print(f'\n  Para {acao}, use [9]:')
    print('    · lotérica fixa (ex.: 9833), ou')
    print('    · * = QUALQUER lotérica + 15 dezenas')
    print('  Abrindo configuração agora (ou CTRL+C para cancelar)...\n')

    if configurar_loterica():
        return True

    print('\n>>> Sem filtro — use [9] no menu antes de [1].')
    return False


def _exigir_modalidade(acao: str = 'extrair') -> bool:
    if ROTULO_ARQUIVO:
        return True
    print('\n' + '=' * 60)
    print('  MODALIDADE NÃO CONFIGURADA')
    print('=' * 60)
    if configurar_modalidade_apenas():
        return bool(ROTULO_ARQUIVO)
    return False


def _cfg_filtro_site() -> FiltroLotericaConfig:
    qtd = 15
    if FILTRO_LOTERICA and FILTRO_LOTERICA.qtd_dezenas:
        qtd = FILTRO_LOTERICA.qtd_dezenas
    return cfg_qualquer_loterica(qtd)


def _inferir_cfg_de_boloes(boloes: list) -> FiltroLotericaConfig:
    if not boloes:
        return _cfg_filtro_site()
    b = boloes[0]
    nome = (b.get('nome_loterica') or '').strip()
    cod_raw = str(b.get('codigo_loterica') or '').strip()
    digits = re.sub(r'\D', '', cod_raw)
    cod = ''
    if digits:
        cod = digits[-4:] if len(digits) >= 4 else digits
    termo = cod or nome[:40] or 'manual'
    return FiltroLotericaConfig(termo=termo, codigo=cod or None, nome=nome or None)


# ─────────────────────────────────────────────────────────────────────────────
# Login / sessão
# ─────────────────────────────────────────────────────────────────────────────

def _payload_tem_usuario(node) -> bool:
    if isinstance(node, dict):
        if node.get('cpf') or node.get('nome'):
            return True
        for val in node.values():
            if _payload_tem_usuario(val):
                return True
    elif isinstance(node, list):
        for item in node:
            if _payload_tem_usuario(item):
                return True
    return False


def _usuario_logado_caixa() -> bool:
    if not _no_site_boloes():
        return False
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            ok = pool.submit(
                driver.execute_script,
                """
                try {
                    var body = (document.body && document.body.innerText) || '';
                    if (/Olá|Ola|Minha conta|Sair/i.test(body)) return true;
                    for (var i = 0; i < localStorage.length; i++) {
                        var k = localStorage.key(i) || '';
                        if (/token|auth|session|access/i.test(k)) {
                            var v = localStorage.getItem(k) || '';
                            if (v.length > 24) return true;
                        }
                    }
                } catch (e) {}
                return false;
                """,
            ).result(timeout=5)
            if ok:
                return True
    except Exception:
        pass
    try:
        for cap in ler_capturas_api(driver):
            url = (cap.get('url') or '').lower()
            if 'recuperar-dados' in url or 'dxn1yXJpb3' in url:
                if _payload_tem_usuario(cap.get('data')):
                    return True
    except Exception:
        pass
    return False


def aguardar_login_caixa() -> bool:
    print('\n' + '=' * 60)
    print('  FAÇA LOGIN (script pausado)')
    print('=' * 60)
    print('\n1. No Edge: LOGIN na Caixa')
    print('2. Abra Bolões Caixa / lista de bolões')
    print('\n3. Volte aqui e pressione ENTER após o login')

    while True:
        try:
            input('\n>>> ENTER após LOGIN no site... ')
        except EOFError:
            return False

        if _usuario_logado_caixa():
            _out('\n  Login OK.')
            return True

        if _no_site_boloes():
            _out('\n  Página de bolões aberta, mas login ainda não confirmado.')
        print('\n  >>> Faça login no Edge e tente de novo.')


def _descartar_enter_fantasma() -> None:
    """Enter colado após escolher opção no menu não deve liberar a extração."""
    try:
        import msvcrt
        time.sleep(0.2)
        while msvcrt.kbhit():
            msvcrt.getwch()
    except Exception:
        pass


def _ler_confirmacao_sim() -> str:
    """
    Lê SIM no terminal — Enter vazio ou colado NÃO conta.
    Windows: leitura caractere a caractere; fallback: input() exige SIM exato.
    """
    print('\n>>> PAUSA: digite SIM e Enter (Enter sozinho NÃO inicia): ', end='', flush=True)
    try:
        import msvcrt
    except ImportError:
        return input().strip().upper()

    buf: list[str] = []
    while True:
        ch = msvcrt.getwche()
        if ch in ('\r', '\n'):
            print(flush=True)
            return ''.join(buf).strip().upper()
        if ch == '\x03':
            raise KeyboardInterrupt
        if ch in ('\b', '\x7f'):
            if buf:
                buf.pop()
                print('\b \b', end='', flush=True)
            continue
        if ch.isprintable():
            buf.append(ch)


def aguardar_site_pronto() -> bool:
    """
    PAUSA rígida: Enter vazio NÃO inicia.
    Só continua após digitar SIM + login confirmado + bolões visíveis na tela.
    """
    print('\n' + '=' * 60)
    print(f'  ⏸⏸⏸  PAUSA — SCRIPT PARADO  [{VERSAO_EXTRATOR}]')
    print('=' * 60)
    print('\n  1. Confirme que está LOGADO no Edge (login automático)')
    print('  2. Escolha SÓ a MODALIDADE no site (sem filtro de estado)')
    print('  3. Volte AQUI e digite SIM (Enter vazio NÃO inicia)')
    print('')
    print('  ⚠  NADA será baixado antes de você digitar SIM.')
    print(f'  JSON: {PASTA_JSON}')
    print('=' * 60)

    _descartar_enter_fantasma()

    while True:
        try:
            resp = _ler_confirmacao_sim()
        except (KeyboardInterrupt, EOFError):
            print('\n  Extração cancelada.')
            return False

        if not resp:
            print('  ⏸  Enter vazio ignorado — digite SIM quando estiver pronto.')
            continue

        if resp in ('N', 'NAO', 'NÃO', 'CANCELAR', 'X', '0'):
            print('  Extração cancelada.')
            return False

        if resp not in ('SIM', 'S', 'OK', 'INICIAR'):
            print('  ⏸  PAUSA mantida — digite SIM (não apenas Enter).')
            continue

        if not _usuario_logado_caixa():
            print('\n  ❌ Login NÃO detectado no Edge. Faça login e digite SIM de novo.')
            continue

        _out('  Verificando bolões visíveis na página...')
        n_det = aguardar_detalhes_visiveis(driver, minimo=1, timeout=12, log_fn=_out)
        if n_det < 1:
            print('\n  ❌ Nenhum bolão na tela (0 botões Detalhes).')
            print('  Faça login, abra a lista de bolões e digite SIM novamente.')
            continue

        _out(f'\n  ✔ Confirmado — login OK, {n_det} bolão(ões) visível(eis). Iniciando...')
        return True


def aguardar_filtro_manual_pagina1(rodada: int = 1) -> bool:
    print('\n' + '=' * 60)
    if rodada == 1:
        print('  FILTRO NO SITE — página 1')
    else:
        print(f'  FILTRO {rodada} — troque no site (mesma sessão logada)')
    print('=' * 60)

    while True:
        try:
            input(f'\n>>> ENTER após filtro aplicado (rodada {rodada}, página 1)... ')
        except EOFError:
            return False
        _out('\n  OK — verificando página de bolões...')
        if _no_site_boloes():
            return True
        url = _driver_url() or '(sem resposta do Edge)'
        _out(f'\n  >>> Não está na lista de bolões. URL atual: {url}')
        _out('  Abra Bolões Caixa no Edge, aplique filtro e tente de novo.')


# ─────────────────────────────────────────────────────────────────────────────
# Filtros / modalidade
# ─────────────────────────────────────────────────────────────────────────────

def _modalidade_do_bolao_item(bolao: dict):
    for chave in ('modalidade_slug', 'modalidade'):
        mod = resolver_modalidade_menu(str(bolao.get(chave) or ''))
        if mod:
            return mod
    texto = str(bolao.get('texto_completo') or '')
    if len(texto) > 20:
        mod = resolver_modalidade_menu(texto[:600])
        if mod:
            return mod
    return None


def _filtrar_boloes_modalidade(boloes: list, mod_esperada) -> tuple[list, int]:
    if not mod_esperada or not boloes:
        return list(boloes), 0
    ok: list = []
    descartados = 0
    for b in boloes:
        mod = _modalidade_do_bolao_item(b)
        if mod is None:
            ok.append(b)
        elif mod.slug == mod_esperada.slug:
            ok.append(b)
        else:
            descartados += 1
    return ok, descartados


def _concurso_de_arquivo_base(arquivo_base: str) -> str:
    """Extrai concurso de boloes_3024_mega-sena → '3024'."""
    m = re.match(r'boloes_(\d+)_', (arquivo_base or '').strip())
    return m.group(1) if m else ''


def _filtrar_boloes_concurso(boloes: list, concurso_alvo: str) -> list:
    if not concurso_alvo:
        return list(boloes)
    alvo = re.sub(r'\D', '', str(concurso_alvo))
    if not alvo:
        return list(boloes)
    return [
        b for b in boloes
        if re.sub(r'\D', '', str(b.get('concurso') or '')) == alvo
    ]


def _boloes_para_json_arquivo(boloes: list, mod_esperada, concurso_alvo: str = '') -> list:
    """
    Bolões que entram em boloes_{concurso}_{modalidade}.json:
    modalidade + concurso — SEM filtro de lotérica (arquivo é da modalidade inteira).
    """
    filtrados, _ = _filtrar_boloes_modalidade(boloes, mod_esperada)
    return _filtrar_boloes_concurso(filtrados, concurso_alvo)


def _salvar_capturas_pagina_disco(pagina: int, rodada: int = 1) -> Optional[str]:
    """Persiste capturas API da página em capturas-api/ (backup + recuperação)."""
    if not driver:
        return None
    caminho = os.path.join(PASTA_CAPTURAS, f'api_r{rodada}_p{pagina}_{int(time.time())}.json')
    try:
        salvar_capturas_brutas(driver, caminho)
        return caminho
    except Exception:
        return None


def _modalidade_extracao(driver=None):
    if ROTULO_ARQUIVO:
        _out(f'  Parser terminal: {ROTULO_ARQUIVO.label}')
        return ROTULO_ARQUIVO
    if driver is not None:
        slug = detectar_modalidade_site(driver)
        if slug:
            mod = resolver_modalidade_menu(slug)
            if mod:
                _out(f'  Modalidade no site: {mod.label}')
                return mod
    _out('  Modalidade: lida da API de cada bolão (campo MEGA_SENA, QUINA…)')
    return None


def _validar_modalidade_coerencia(mod_esperada, boloes: list) -> None:
    if not boloes:
        return
    mod_json = extrair_modalidade_de_boloes(boloes)
    label_json = mod_json.label if mod_json else str(boloes[0].get('modalidade') or '?')
    label_site = mod_esperada.label if mod_esperada else '(não definida)'
    concurso = extrair_concurso_de_boloes(boloes)

    if mod_esperada and mod_json and mod_esperada.slug != mod_json.slug:
        _out(f'\n  ERRO: Modalidade site/terminal ({label_site}) difere da gravada no JSON ({label_json}).')

    if mod_esperada and mod_json:
        arq_ok = nome_arquivo_consolidado_padrao(concurso, mod_esperada)
        arq_json = nome_arquivo_consolidado_padrao(concurso, mod_json)
        if arq_ok != arq_json:
            _out(f'  ERRO: Nome do arquivo ({arq_json}) não bate com modalidade do site ({arq_ok}).')
        else:
            _out(f'  OK modalidade: {label_site} | concurso {concurso} | {arq_ok}')


def _renomear_json_sessao(arquivo_base: str, boloes: list, mod) -> str:
    if not boloes:
        return arquivo_base
    novo = nome_arquivo_sessao(extrair_concurso_de_boloes(boloes), extrair_modalidade_de_boloes(boloes) or mod)
    if novo == arquivo_base:
        return arquivo_base
    antigo = os.path.join(PASTA_JSON, f'{arquivo_base}.json')
    destino = os.path.join(PASTA_JSON, f'{novo}.json')
    if os.path.isfile(antigo) and antigo != destino:
        if os.path.isfile(destino):
            existentes = carregar_json_boloes(destino)
            sessao_antigo = carregar_json_boloes(antigo)
            final, _ = mesclar_listas(existentes, sessao_antigo + boloes)
            salvar_json_boloes(destino, final)
            os.remove(antigo)
        else:
            os.rename(antigo, destino)
        _out(f'  Arquivo renomeado: {os.path.basename(destino)}')
    return novo


def _iniciar_continuidade_inteligente(
    arquivo_base: str,
    mod_esperada,
    painel: dict,
) -> Tuple[set, str]:
    mod_slug = mod_esperada.slug if mod_esperada else ''
    path, existentes = localizar_arquivo_sessao_existente(PASTA_JSON, arquivo_base, mod_slug)
    if not existentes:
        return set(), arquivo_base

    hashes = hashes_de_lista(existentes)
    arquivo_efetivo = os.path.splitext(os.path.basename(path))[0]
    # Nunca gravar em *_CONSOLIDADO — esse nome é só backup/espelho
    if arquivo_efetivo.endswith('_CONSOLIDADO'):
        arquivo_efetivo = arquivo_efetivo[: -len('_CONSOLIDADO')]
    kb = os.path.getsize(path) / 1024 if path else 0
    painel['continuidade'] = {
        'path': path,
        'arquivo': f'{arquivo_efetivo}.json',
        'existentes': len(existentes),
        'kb': round(kb, 1),
    }
    _out(f'  [CONTINUIDADE] fonte={os.path.basename(path)} | {len(existentes)} reg. ({kb:.1f} KB)')
    _out(f'  [CONTINUIDADE] gravando em: {arquivo_efetivo}.json')
    # Se a sessão estiver ausente/corrompida e a fonte for CONSOLIDADO, restaura
    destino_sessao = os.path.join(PASTA_JSON, f'{arquivo_efetivo}.json')
    if path and os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.abspath(destino_sessao)):
        if existentes:
            salvar_json_boloes(destino_sessao, existentes)
            _out(f'  [CONTINUIDADE] Sessao restaurada a partir do backup.')
    return hashes, arquivo_efetivo


def preparar_login_unico() -> bool:
    global SESSAO_AUTORIZADA
    SESSAO_AUTORIZADA = False
    if not iniciar_navegador():
        return False
    print('\n  Edge aberto — faça LOGIN (script aguarda, nada roda ainda).')
    if not aguardar_login_caixa():
        return False
    if not _usuario_logado_caixa():
        print('\n>>> Login não confirmado. Extração cancelada.')
        return False
    print('\n  Sessão logada — pronta para configurar filtros no site.')
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Salvar parcial (tempo real) — exibe KBs no terminal
# ─────────────────────────────────────────────────────────────────────────────

def salvar_parcial(boloes, arquivo_base, pagina: int = 0):
    path = os.path.join(PASTA_JSON, f'{arquivo_base}.json')
    if not boloes:
        if os.path.isfile(path):
            kb = os.path.getsize(path) / 1024
            _out(
                f'  [SAVE] Pág {pagina or "?"}: 0 novos nesta leva — '
                f'arquivo mantém {len(carregar_json_boloes(path))} reg. ({kb:.1f} KB).'
            )
        else:
            _out(f'  [SAVE] Pág {pagina or "?"}: 0 reg. — arquivo ainda não criado.')
        return path

    sem_hash = sum(1 for b in boloes if not b.get('hash_bolao'))
    if sem_hash:
        _out(f'  [SAVE AVISO] {sem_hash} bolão(ões) sem hash — não entram no JSON.')

    final, novos, anteriores = salvar_json_continuacao(path, boloes)
    kb = os.path.getsize(path) / 1024 if os.path.isfile(path) else 0
    _out(
        f'  💾 SALVO pág {pagina or "?"}: {len(final)} reg. total (+{novos} novos) | '
        f'{kb:.1f} KB | {os.path.basename(path)}'
    )
    return path


def _atualizar_arquivo_base_concurso(arquivo_base: str, concurso: str, mod_esperada) -> str:
    """Renomeia boloes_sem-concurso_* → boloes_{concurso}_* assim que o concurso for detectado."""
    conc = re.sub(r'\D', '', str(concurso or ''))
    if not conc or not mod_esperada:
        return arquivo_base
    novo = nome_arquivo_sessao(conc, mod_esperada)
    if novo == arquivo_base:
        return arquivo_base
    antigo = os.path.join(PASTA_JSON, f'{arquivo_base}.json')
    destino = os.path.join(PASTA_JSON, f'{novo}.json')
    if os.path.isfile(antigo) and antigo != destino:
        if os.path.isfile(destino):
            existentes = carregar_json_boloes(destino)
            sessao = carregar_json_boloes(antigo)
            final, _ = mesclar_listas(existentes, sessao)
            salvar_json_boloes(destino, final)
            os.remove(antigo)
        else:
            os.rename(antigo, destino)
        _out(f'  [ARQUIVO] Renomeado → {novo}.json')
    return novo


def _persistir_json_pagina(
    pagina: int,
    rodada: int,
    arquivo_base: str,
    painel: dict,
    mod_esperada,
    parser_slug: str,
    hashes: set,
    boloes: list,
) -> Tuple[int, str]:
    """
    Gravação garantida após cada página:
    1) salva capturas API em disco
    2) extrai bolões do arquivo de captura
    3) mescla no JSON de sessão (modalidade + concurso)
    """
    from boloes_consolidar import boloes_de_capturas_api
    from boloes_api_caixa import coletar_boloes_das_capturas

    path_cap = _salvar_capturas_pagina_disco(pagina, rodada)
    candidatos: list = []

    if path_cap and os.path.isfile(path_cap):
        candidatos = boloes_de_capturas_api([path_cap])

    if not candidatos and driver:
        candidatos = coletar_boloes_das_capturas(
            driver, set(), None, None, parser_slug, filtrar_dezenas=False,
        )

    conc = painel.get('concurso_alvo') or _concurso_de_arquivo_base(arquivo_base)
    alvo = _boloes_para_json_arquivo(candidatos, mod_esperada, conc)

    if not conc and alvo:
        conc = extrair_concurso_de_boloes(alvo)
        painel['concurso_alvo'] = conc
        alvo = _boloes_para_json_arquivo(candidatos, mod_esperada, conc)

    if conc and mod_esperada:
        arquivo_base = _atualizar_arquivo_base_concurso(arquivo_base, conc, mod_esperada)
        painel['arquivo_base'] = arquivo_base

    if not alvo:
        path = os.path.join(PASTA_JSON, f'{arquivo_base}.json')
        total = len(carregar_json_boloes(path))
        kb = os.path.getsize(path) / 1024 if os.path.isfile(path) else 0
        caps = os.path.basename(path_cap) if path_cap else '—'
        _out(
            f'  [SAVE] Pág {pagina}: 0 bolões parseáveis (captura: {caps}) | '
            f'arquivo: {total} reg. ({kb:.1f} KB)'
        )
        return 0, arquivo_base

    for b in alvo:
        b['pagina'] = pagina
        b['rodada_filtro'] = rodada
        if painel.get('uf_varredura'):
            b['uf_varredura'] = painel['uf_varredura']

    hashes_antes = set(hashes)
    novos_gravar = [b for b in alvo if (b.get('hash_bolao') or '') not in hashes_antes]

    salvar_parcial(alvo, arquivo_base, pagina)

    for b in novos_gravar:
        h = b.get('hash_bolao')
        if h:
            hashes.add(h)
        b['indice'] = len(boloes) + 1
        boloes.append(b)

    return len(novos_gravar), arquivo_base


# ─────────────────────────────────────────────────────────────────────────────
# Captura de página
# ─────────────────────────────────────────────────────────────────────────────

def _capturas_da_rodada(rodada: int) -> list[str]:
    pat = os.path.join(PASTA_CAPTURAS, f'api_r{rodada}_p*.json')
    return sorted(glob.glob(pat))


def _recuperar_boloes_das_capturas(
    cfg,
    parser_slug,
    mod_slug,
    arquivo_base,
    rodada=1,
    mod_esperada=None,
    concurso_alvo: str = '',
    aplicar_filtro_loterica: bool = False,
):
    """Recupera bolões das capturas em disco → JSON de sessão (modalidade+concurso)."""
    from boloes_consolidar import boloes_de_capturas_api

    arquivos = _capturas_da_rodada(rodada)
    if not arquivos:
        arquivos = sorted(glob.glob(os.path.join(PASTA_CAPTURAS, 'api_r*_p*.json')))
    if not arquivos:
        return []

    conc = concurso_alvo or _concurso_de_arquivo_base(arquivo_base)
    cod_lot = cfg.codigo if (aplicar_filtro_loterica and not cfg.qualquer_loterica) else None
    qtd_dez = cfg.qtd_dezenas if aplicar_filtro_loterica else None
    brutos = boloes_de_capturas_api(arquivos, cod_lot, qtd_dez)
    boloes = _boloes_para_json_arquivo(brutos, mod_esperada, conc)
    if aplicar_filtro_loterica:
        boloes = _boloes_do_filtro(boloes, cfg)
    elif not boloes and brutos:
        _out(f'  [RECUPERO] {len(brutos)} bolão(ões) na API, mas 0 para modalidade/concurso alvo.')

    if boloes:
        path = os.path.join(PASTA_JSON, f'{arquivo_base}.json')
        final, novos, anteriores = salvar_json_continuacao(path, boloes)
        # Mantém um único JSON de sessão (sem espelho _CONSOLIDADO)
        kb = os.path.getsize(path) / 1024 if os.path.isfile(path) else 0
        _out(
            f'\n  [RECUPERO] {len(final)} reg. no arquivo (+{novos} novos) | '
            f'{kb:.1f} KB | {len(arquivos)} captura(s) API.'
        )
        return boloes
    return []


def _diagnosticar_capturas_sem_filtro(cfg, parser_slug) -> None:
    if not driver:
        return
    from boloes_api_caixa import coletar_boloes_das_capturas

    todos = coletar_boloes_das_capturas(driver, set(), print, None, parser_slug, filtrar_dezenas=False)
    if not todos:
        _out('  [DIAG] Nenhum bolão parseável nas capturas API desta página.')
        return
    lotericas = {}
    for b in todos:
        nome = (b.get('nome_loterica') or '?')[:40]
        lotericas[nome] = lotericas.get(nome, 0) + 1
    _out(f'  [DIAG] API tem {len(todos)} bolão(ões) SEM filtro de lotérica:')
    for nome, q in sorted(lotericas.items(), key=lambda x: -x[1])[:6]:
        _out(f'         · {q}× {nome}')
    if cfg and cfg.termo:
        _out(f'  [DIAG] Filtro ativo: {cfg.termo} — confira se bate com a lotérica no site.')


def _boloes_do_filtro(boloes: list, cfg: FiltroLotericaConfig) -> list:
    if not cfg:
        return list(boloes)
    if cfg.qualquer_loterica or (
        not (cfg.termo or '').strip() and not cfg.codigo and cfg.qtd_dezenas is not None
    ):
        return [b for b in boloes if bolao_atende_filtro(b, cfg)]
    if not cfg.termo and not cfg.codigo:
        return []
    if cfg.qtd_dezenas is not None:
        return [b for b in boloes if bolao_atende_filtro(b, cfg)]
    return [b for b in boloes if bolao_corresponde_loterica(b, cfg)]


def _capturar_pagina_atual(
    cfg, parser_slug, hashes, pagina, boloes, manual, painel, mod_esperada=None, arquivo_base='',
) -> int:
    if not SESSAO_AUTORIZADA:
        print('  [SESSÃO] Captura bloqueada — conclua login + filtro manual antes.')
        return -1
    if not garantir_sessao_caixa(driver, pagina, print):
        print('  [SESSÃO] Extração interrompida — faça login e rode de novo.')
        return -1

    if pagina == 1:
        print('  [FILTRO] Página 1 — aguardando botões Detalhes...')
        n_det = aguardar_detalhes_visiveis(driver, minimo=1, timeout=12, log_fn=print)
        if n_det:
            print(f'  [TELA] {n_det} botão(ões) Detalhes visíveis.')
        else:
            print('  [TELA] Nenhum botão Detalhes detectado — confira filtro no site.')
        time.sleep(0.8)
    else:
        meta_preservar = ler_metadados_paginacao_api(driver)
        if meta_preservar:
            painel['paginacao_api'] = meta_preservar
        limpar_capturas_api(driver)
        if painel.pop('pular_navegacao_proxima', False):
            print(f'  [CHECKPOINT] Já posicionado na página {pagina} — extraindo sem avançar.')
            time.sleep(0.8)
            n_det = aguardar_detalhes_visiveis(driver, minimo=1, timeout=12)
            if n_det:
                print(f'  [TELA] Página {pagina}: {n_det} botão(ões) Detalhes visíveis.')
        elif not manual:
            print(f'  [PÁGINA] Avançando para página {pagina} (Seguinte)...')
            if not ir_proxima_pagina_lista(driver, print):
                if cfg.termo:
                    if not preparar_pagina_loterica(driver, cfg, pagina, print):
                        print('  [FILTRO] Falha ao preparar página.')
                        return -1
                elif ultima_pagina_detectada(driver) or eh_ultima_pagina(driver):
                    return -2
                else:
                    meta_nav = ler_metadados_paginacao_api(driver)
                    ultima = (meta_nav or {}).get('ultima_pagina') or 0
                    if pagina <= ultima and ir_para_pagina_lista(driver, pagina, print):
                        print(f'  [PÁGINA] Navegou para página {pagina} (fallback Angular).')
                    elif ultima_pagina_detectada(driver) or eh_ultima_pagina(driver):
                        return -2
                    else:
                        print(f'  [PÁGINA] Seguinte falhou ao ir para página {pagina}.')
                        return -1
            time.sleep(1.2)
            n_det = aguardar_detalhes_visiveis(driver, minimo=1, timeout=12)
            if n_det:
                print(f'  [TELA] Página {pagina}: {n_det} botão(ões) Detalhes visíveis.')
        else:
            print(f'  [FILTRO] Página {pagina} — modo manual (você navegou).')

    aguardar_capturas_api(driver, minimo=1, timeout=12)
    preparar_pagina_para_detalhes(driver, log_fn=print)
    meta = detectar_detalhes_pagina(driver, cfg, 55, preparar=False, log_fn=print)
    n_esperado = meta['n_esperado']
    codigos = meta['codigos']

    # Retomada intra-página: quantos desta página já estão no JSON
    ab_ck = painel.get('arquivo_base') or arquivo_base
    path_json_pag = os.path.join(PASTA_JSON, f'{ab_ck}.json') if ab_ck else ''
    existentes_pag = carregar_json_boloes(path_json_pag) if path_json_pag else []
    ja_coletados = sum(1 for b in existentes_pag if b.get('pagina') == pagina)
    if ja_coletados:
        print(
            f'  [CHECKPOINT] JSON já tem {ja_coletados} bolão(ões) da página {pagina}'
            + (f' (meta tela={n_esperado}).' if n_esperado else '.')
        )

    if n_esperado:
        print(f'  [TELA] Meta desta página: {n_esperado} bolão(ões).')
        if ja_coletados >= n_esperado:
            print('  [TELA] Página já coberta no JSON — não inicia cliques.')
        else:
            print('  [TELA] Iniciando cliques em Detalhes...')
    else:
        print('  [TELA] Nenhum Detalhes visível — tentando lista API interceptada...')

    # ── Callback: grava no JSON em tempo real a cada bloco de detalhes ───────
    # IMPORTANTE: NÃO faz boloes.append aqui — isso é feito pelo loop principal
    # depois que detalhar_pagina_ate_esperado retorna. O callback só grava no disco.
    concurso_alvo = painel.get('concurso_alvo') or _concurso_de_arquivo_base(arquivo_base)

    def _salvar_tempo_real(boloes_parciais):
        if not arquivo_base or not boloes_parciais:
            return
        ca = painel.get('concurso_alvo') or concurso_alvo
        alvo = _boloes_para_json_arquivo(boloes_parciais, mod_esperada, ca)
        if alvo:
            for b in alvo:
                b['pagina'] = pagina
                b['rodada_filtro'] = painel.get('rodada_filtro', 1)
            salvar_parcial(alvo, painel.get('arquivo_base') or arquivo_base, pagina)

    detalhar_pagina_ate_esperado(
        driver, cfg, parser_slug, hashes, n_esperado, codigos, print,
        on_progresso=_salvar_tempo_real if arquivo_base else None,
        ja_coletados=ja_coletados,
    )

    n_caps = len(ler_capturas_api(driver))
    painel['capturas_ultima_pagina'] = n_caps
    painel['capturas_api'] += n_caps
    meta_pag = ler_metadados_paginacao_api(driver)
    if meta_pag:
        painel['paginacao_api'] = meta_pag
    painel['detalhes_tela_pagina'] = n_esperado

    ab = painel.get('arquivo_base') or arquivo_base
    n_gravados, ab = _persistir_json_pagina(
        pagina, painel.get('rodada_filtro', 1), ab, painel, mod_esperada, parser_slug, hashes, boloes,
    )
    painel['arquivo_base'] = ab

    # Contagem efetiva na página (disco + novos desta passagem)
    existentes_apos = carregar_json_boloes(os.path.join(PASTA_JSON, f'{ab}.json')) if ab else []
    n_pagina_json = sum(1 for b in existentes_apos if b.get('pagina') == pagina)
    n_efetivo = max(n_gravados, n_pagina_json, ja_coletados)

    novos_loterica = _boloes_do_filtro(
        [b for b in boloes if b.get('pagina') == pagina], cfg,
    )
    if cfg.termo and n_efetivo and len(novos_loterica) != n_gravados and n_gravados:
        _out(
            f'  [FILTRO] Lotérica alvo nesta pág.: {len(novos_loterica)} de {n_gravados} '
            f'(todos {n_gravados} foram gravados no JSON da modalidade).'
        )

    if not n_gravados and n_caps > 0 and n_pagina_json < (n_esperado or 1):
        _diagnosticar_capturas_sem_filtro(cfg, parser_slug)

    painel['pendentes_pagina'] = max(0, n_esperado - n_efetivo) if n_esperado else 0
    painel['pagina_completa'] = bool(n_esperado and n_efetivo >= n_esperado) or (not n_esperado and n_efetivo > 0)
    if n_esperado and n_efetivo < n_esperado:
        print(f'  [AVISO] Página incompleta: {n_efetivo}/{n_esperado} bolões no JSON desta página.')
    elif n_esperado and n_efetivo >= n_esperado and n_gravados == 0 and ja_coletados:
        print(f'  [CHECKPOINT] Página {pagina} confirmada no JSON: {n_efetivo}/{n_esperado}.')

    return n_efetivo


# ─────────────────────────────────────────────────────────────────────────────
# Loop principal de páginas
# ─────────────────────────────────────────────────────────────────────────────

def _loop_extracao_paginas(
    cfg, parser_slug, mod_slug, arquivo_base,
    manual_paginas, rodada_filtro=1, voce_encerra=False,
    painel_extra=None, mod_esperada=None, concurso_alvo: str = '',
    forcar_pagina_inicial: Optional[int] = None,
    uf_varredura: str = '',
    ufs_concluidas: Optional[list] = None,
    marcar_concluido_ao_fim: bool = True,
):
    boloes: list = []
    hashes: set = set()
    hashes_pagina_anterior: set = set()
    painel = _novo_painel_extracao()
    painel['rodada_filtro'] = rodada_filtro
    painel['arquivo_base'] = arquivo_base
    painel['concurso_alvo'] = concurso_alvo or _concurso_de_arquivo_base(arquivo_base)
    if uf_varredura:
        painel['uf_varredura'] = uf_varredura
    if painel_extra:
        painel.update(painel_extra)
    inicio = time.time()
    pagina = 1
    pausado = False
    chegou_ao_fim = False
    ufs_ok = list(ufs_concluidas or [])

    def _extra_ck() -> dict:
        extra = {}
        if uf_varredura:
            extra['uf_atual'] = uf_varredura
        if ufs_ok:
            extra['ufs_concluidas'] = list(ufs_ok)
        return extra

    limpar_capturas_api(driver)
    _out('  [API] Capturas anteriores limpas — só dados desta extração.')

    hashes_base, arquivo_base = _iniciar_continuidade_inteligente(arquivo_base, mod_esperada, painel)
    hashes.update(hashes_base)
    painel['arquivo_base'] = arquivo_base
    if not painel.get('concurso_alvo'):
        painel['concurso_alvo'] = _concurso_de_arquivo_base(arquivo_base)

    # ── Checkpoint / retomada (não altera login nem parsing) ─────────────────
    path_existente = os.path.join(PASTA_JSON, f'{arquivo_base}.json')
    existentes_disco = carregar_json_boloes(path_existente)
    # Se a sessão estiver vazia/corrompida, tenta CONSOLIDADO / localizar
    if not existentes_disco:
        path_alt, alt = localizar_arquivo_sessao_existente(
            PASTA_JSON, arquivo_base,
            mod_esperada.slug if mod_esperada else mod_slug,
            painel.get('concurso_alvo') or '',
        )
        if alt:
            existentes_disco = alt
            _out(f'  [CHECKPOINT] Usando backup/localizado: {os.path.basename(path_alt or "")} ({len(alt)} reg.)')
            # restaura sessão a partir do backup legível
            if path_alt and path_alt != path_existente and alt:
                salvar_json_boloes(path_existente, alt)
                _out(f'  [CHECKPOINT] Sessão restaurada → {os.path.basename(path_existente)}')

    if forcar_pagina_inicial is not None:
        pagina = max(1, int(forcar_pagina_inicial))
        retomou = pagina > 1
        if retomou:
            _out(f'  [UF] Retomando {uf_varredura or "?"} na página {pagina}')
        else:
            _out(f'  [UF] Iniciando {uf_varredura or "?"} na página 1')
    else:
        pagina, retomou = perguntar_retomada(
            PASTA_JSON, arquivo_base, existentes_disco, out_fn=_out,
        )
    reset_pause_flags(PASTA_JSON)
    instruir_pause(PASTA_JSON, _out)

    if retomou and pagina > 1 and not manual_paginas:
        _out(f'  [CHECKPOINT] Indo DIRETO para a pagina {pagina}...')
        ir_direto_para_pagina_lista(driver, pagina, print)
        from boloes_filtro_loterica import _ler_pagina_atual_ui
        atual_ui = _ler_pagina_atual_ui(driver)
        tentativas_manual = 0
        while (atual_ui or 0) != pagina:
            tentativas_manual += 1
            _out('')
            _out('!' * 60)
            _out(f'  ATENCAO: Edge esta na pagina {atual_ui or "?"} — precisamos da {pagina}.')
            _out(f'  1) No Edge, use a paginacao e va ate a pagina {pagina}')
            _out('  2) Volte neste terminal')
            _out(f'  3) Digite OK e Enter (ou so Enter para tentar de novo)')
            _out('  Digite FORCAR + Enter para extrair mesmo assim (nao recomendado)')
            _out('!' * 60)
            try:
                resp_pag = input('>>> OK / FORCAR / Enter: ').strip().upper()
            except EOFError:
                resp_pag = 'FORCAR'
            if resp_pag in ('FORCAR', 'F', 'FORCE'):
                _out(f'  [CHECKPOINT] FORCAR — extraindo a pagina visivel como se fosse {pagina}.')
                break
            ir_direto_para_pagina_lista(driver, pagina, print)
            atual_ui = _ler_pagina_atual_ui(driver)
            if (atual_ui or 0) == pagina or resp_pag in ('OK', 'C', 'SIM'):
                if (atual_ui or 0) == pagina:
                    break
                if resp_pag in ('OK', 'C', 'SIM'):
                    _out('  [CHECKPOINT] OK aceito — seguindo.')
                    break
            if tentativas_manual >= 5:
                _out('  [CHECKPOINT] Muitas tentativas — use FORCAR ou ajuste o Edge.')
        painel['pular_navegacao_proxima'] = True
        _out(f'  [CHECKPOINT] Iniciando extracao a partir da pagina {pagina}.')
        salvar_checkpoint(
            PASTA_JSON,
            modalidade=(mod_esperada.slug if mod_esperada else mod_slug) or '',
            modalidade_label=(mod_esperada.label if mod_esperada else '') or '',
            concurso=painel.get('concurso_alvo') or '',
            arquivo_base=arquivo_base,
            pagina_atual=pagina - 1,
            total_paginas=218,
            boloes_extraidos=len(existentes_disco),
            status=STATUS_EXECUTANDO,
            extra=_extra_ck(),
        )

    dez = cfg.qtd_dezenas or 'qualquer'
    lot_txt = 'QUALQUER lotérica' if cfg.qualquer_loterica else (cfg.termo or '(filtro manual no site)')
    conc_txt = painel.get('concurso_alvo') or 'auto'
    print('\n  [PAINEL] Contadores: páginas | registros/página | total | únicos')
    print(f'  Filtro lotérica: {lot_txt} | dezenas: {dez} (painel/resumo)')
    print(f'  JSON arquivo  : json-boloes/{arquivo_base}.json  (modalidade + concurso {conc_txt})')
    print('  Gravação      : tempo real — um único JSON de sessão (sem CONSOLIDADO duplicado)')
    if mod_esperada:
        print(f'  Modalidade alvo: {mod_esperada.label}')
    if retomou:
        print(f'  Retomada      : iniciando na página {pagina}')

    while True:
        if manual_paginas and pagina > 1:
            try:
                resp = input(
                    f'\n>>> [{cfg.termo}] PÁGINA {pagina} no site — '
                    f'navegue e ENTER | FIM=acabou | P=pausar: '
                ).strip().upper()
            except EOFError:
                break
            if resp in ('P', 'PAUSAR', 'PAUSE'):
                pausado = True
                _out('  [CHECKPOINT] Pausado pelo usuário.')
                break
            if resp == 'FIM':
                print('  Fim deste filtro (você encerrou).')
                break

        print(f'\n>>> Processando PÁGINA {pagina}...')
        n_novos = _capturar_pagina_atual(
            cfg, parser_slug, hashes, pagina, boloes, manual_paginas, painel, mod_esperada,
            arquivo_base=arquivo_base,
        )
        arquivo_base = painel.get('arquivo_base') or arquivo_base
        if pagina == 1 and n_novos == 0 and (painel.get('detalhes_tela_pagina') or 0) < 1:
            print('\n  [ABORTADO] Página 1 sem bolões visíveis — login ou lista não pronta.')
            print('  Nada foi baixado. Faça login no site e rode [1] de novo.')
            break
        if n_novos == -2:
            print(f'\n  {MSG_ULTIMA_PAGINA}')
            chegou_ao_fim = True
            break
        if n_novos < 0:
            print('\n  Extração interrompida (sessão).')
            break

        page_boloes = [b for b in boloes if b.get('pagina') == pagina]
        page_loterica = _boloes_do_filtro(page_boloes, cfg)
        h_pag = hashes_pagina(page_boloes)
        if n_novos and h_pag and h_pag == hashes_pagina_anterior:
            print('  [AVISO] Página igual à anterior — confira navegação.')
        hashes_pagina_anterior = h_pag

        _imprimir_painel_pagina(pagina, len(page_boloes), boloes, hashes, painel)
        if page_loterica and cfg.termo:
            print(f'  [FILTRO] Lotérica alvo nesta pág.: {len(page_loterica)} de {len(page_boloes)}')

        conc = painel.get('concurso_alvo') or _concurso_de_arquivo_base(arquivo_base)
        subset_arquivo = _boloes_para_json_arquivo(boloes, mod_esperada, conc)
        if not conc and subset_arquivo:
            conc = extrair_concurso_de_boloes(subset_arquivo)
            painel['concurso_alvo'] = conc
            subset_arquivo = _boloes_para_json_arquivo(boloes, mod_esperada, conc)

        if subset_arquivo:
            novo_base = _renomear_json_sessao(arquivo_base, subset_arquivo, mod_esperada)
            if novo_base != arquivo_base:
                arquivo_base = novo_base
                painel['arquivo_base'] = arquivo_base
                if not painel.get('concurso_alvo'):
                    painel['concurso_alvo'] = _concurso_de_arquivo_base(arquivo_base)

        path_json = os.path.join(PASTA_JSON, f'{arquivo_base}.json')
        total_disco = len(carregar_json_boloes(path_json))
        if total_disco:
            kb = os.path.getsize(path_json) / 1024
            _out(f'  📁 Arquivo: {total_disco} reg. | {kb:.1f} KB | {os.path.basename(path_json)}')

        # Checkpoint: só avança pagina_atual se a página estiver completa no JSON.
        meta_pag = ler_metadados_paginacao_api(driver) or painel.get('paginacao_api') or {}
        total_paginas = int(meta_pag.get('ultima_pagina') or 0)
        if meta_pag:
            painel['paginacao_api'] = meta_pag
            pa = int(meta_pag.get('pagina_atual') or pagina)
            up = int(meta_pag.get('ultima_pagina') or pagina)
            print(f'  [PÁGINA] API: página {pa} de {up} ({meta_pag.get("total_registros", "?")} bolões).')

        n_esp = int(painel.get('detalhes_tela_pagina') or 0)
        pagina_ok = bool(painel.get('pagina_completa'))
        if not pagina_ok and n_esp:
            existentes_ck = carregar_json_boloes(path_json)
            n_pag_ck = sum(1 for b in existentes_ck if b.get('pagina') == pagina)
            pagina_ok = n_pag_ck >= n_esp
            if not pagina_ok:
                _out(
                    f'  [CHECKPOINT] Página {pagina} incompleta '
                    f'({n_pag_ck}/{n_esp}) — retomada permanecerá nesta página.'
                )

        pagina_checkpoint = pagina if (pagina_ok or not n_esp) else max(0, pagina - 1)
        salvar_checkpoint(
            PASTA_JSON,
            modalidade=(mod_esperada.slug if mod_esperada else mod_slug) or '',
            modalidade_label=(mod_esperada.label if mod_esperada else '') or '',
            concurso=painel.get('concurso_alvo') or '',
            arquivo_base=arquivo_base,
            pagina_atual=pagina_checkpoint,
            total_paginas=total_paginas,
            boloes_extraidos=total_disco or len(hashes),
            status=STATUS_EXECUTANDO,
            extra=_extra_ck(),
        )
        if pagina_checkpoint >= pagina:
            _out(f'  [CHECKPOINT] Página {pagina} gravada — próximo retomaria em {pagina + 1}.')
        else:
            _out(
                f'  [CHECKPOINT] Página {pagina} incompleta no checkpoint — '
                f'próximo retomaria na página {pagina} (não em {pagina + 1}).'
            )

        if voce_encerra:
            print(
                f'\n  Página {pagina} concluída ({len(page_boloes)} reg. no JSON | '
                f'{len(page_loterica)} da lotérica alvo). '
                f'Arquivo: {total_disco} reg.'
            )
            print(f'  Próxima página? Navegue no site e ENTER. Era a última? Digite FIM.')

        if pause_solicitada(PASTA_JSON):
            pausado = True
            _out('\n  [CHECKPOINT] PAUSA — página atual concluída. Pode fechar e continuar depois.')
            break

        if not voce_encerra:
            if ultima_pagina_detectada(driver):
                print(f'\n  {MSG_ULTIMA_PAGINA}')
                chegou_ao_fim = True
                break

        pagina += 1

    tempo = int(time.time() - inicio)
    conc = painel.get('concurso_alvo') or _concurso_de_arquivo_base(arquivo_base)
    recuperados = _recuperar_boloes_das_capturas(
        cfg, parser_slug, mod_slug, arquivo_base, rodada_filtro,
        mod_esperada=mod_esperada, concurso_alvo=conc,
    )
    path_json = os.path.join(PASTA_JSON, f'{arquivo_base}.json')
    subset_final = carregar_json_boloes(path_json)
    if recuperados:
        boloes = recuperados
    elif not subset_final:
        subset_final = _boloes_para_json_arquivo(boloes, mod_esperada, conc)

    subset_loterica = _boloes_do_filtro(boloes, cfg)
    painel['registros_loterica_alvo'] = len(subset_loterica)

    total_final = len(subset_final) if subset_final else len(carregar_json_boloes(path_json))
    meta_fim = painel.get('paginacao_api') or {}
    total_paginas_fim = int(meta_fim.get('ultima_pagina') or painel.get('paginas_processadas') or 0)
    ultima_ok = int(painel.get('paginas_processadas') or 0)

    if ultima_ok > 0:
        # UF só é "concluída" com fim REAL (Seguinte desabilitado).
        # NÃO usar pagina >= total_paginas da API — esse total pode estar errado
        # (ex.: total=148 com página 161 ainda válida) e marcava SP como ok cedo demais.
        if pausado:
            status_fim = STATUS_PAUSADO
            painel['uf_concluida'] = False
        elif chegou_ao_fim:
            if uf_varredura and uf_varredura not in ufs_ok:
                ufs_ok.append(uf_varredura)
            status_fim = STATUS_CONCLUIDO if marcar_concluido_ao_fim else STATUS_EXECUTANDO
            painel['uf_concluida'] = True
        else:
            # Interrompido / sessão / abort — mantém UF em andamento (não entra em ufs_concluidas)
            status_fim = STATUS_PAUSADO
            painel['uf_concluida'] = False
        # Ao pausar/interromper: garanta que a UF atual NÃO fique em ufs_concluidas
        if not painel.get('uf_concluida') and uf_varredura and uf_varredura in ufs_ok:
            ufs_ok = [u for u in ufs_ok if u != uf_varredura]
        salvar_checkpoint(
            PASTA_JSON,
            modalidade=(mod_esperada.slug if mod_esperada else mod_slug) or '',
            modalidade_label=(mod_esperada.label if mod_esperada else '') or '',
            concurso=painel.get('concurso_alvo') or '',
            arquivo_base=arquivo_base,
            pagina_atual=ultima_ok,
            total_paginas=total_paginas_fim,
            boloes_extraidos=total_final,
            status=status_fim,
            extra=_extra_ck(),
        )
        if status_fim == STATUS_PAUSADO:
            _out(f'\n  [CHECKPOINT] Status=Pausado | última OK={ultima_ok} | próximo={ultima_ok + 1}')
        elif status_fim == STATUS_CONCLUIDO:
            _out(f'\n  [CHECKPOINT] Status=Concluído | páginas={ultima_ok}')
        elif painel.get('uf_concluida'):
            _out(
                f'\n  [CHECKPOINT] UF {uf_varredura or "?"} ok | páginas={ultima_ok} | '
                f'próximas UFs: {27 - len(ufs_ok)}'
            )
        else:
            _out(
                f'\n  [CHECKPOINT] Status={status_fim} | UF {uf_varredura or "?"} '
                f'pág. {ultima_ok} (ainda em andamento)'
            )
        reset_pause_flags(PASTA_JSON)

    if painel.get('paginas_com_dados', 0) == 0:
        print('\n  [AVISO] Nenhum bolão baixado nesta sessão — extração não consolidou nada novo.')
        _imprimir_resumo_final([], hashes, painel, arquivo_base, cfg, tempo)
        return [], hashes, painel, arquivo_base

    _imprimir_resumo_final(subset_final, hashes, painel, arquivo_base, cfg, tempo)
    if subset_final:
        _out(f'\n  Arquivo final: {path_json}')
        _out('  (Arquivo único de sessão — não há mais gravação CONSOLIDADO duplicada.)')
    elif painel.get('capturas_api', 0) > 0:
        _out('\n  [AVISO] Extração vazia apesar de capturas API — veja [DIAG] acima.')
    return subset_final, hashes, painel, arquivo_base


def _consolidar_e_resumir(boloes_sessao, mod_esperada):
    """
    Legado: o espelho *_CONSOLIDADO.json era idêntico ao JSON de sessão.
    Mantido apenas para o menu manual de consolidação de capturas.
    Na extração automática NÃO é mais chamado a cada página.
    """
    if not boloes_sessao:
        return None, []
    mod_json = extrair_modalidade_de_boloes(boloes_sessao) or mod_esperada
    concurso = extrair_concurso_de_boloes(boloes_sessao)
    mod_ref = mod_json or mod_esperada
    mod_slug = mod_ref.slug if mod_ref else 'boloes'
    _validar_modalidade_coerencia(mod_esperada, boloes_sessao)
    path, final, novos = consolidar_sessao(PASTA_JSON, concurso, mod_slug, boloes_sessao)
    print(f'\n  CONSOLIDADO (opcional/menu): {path}')
    print(f'  Sessão: {len(boloes_sessao)} | +{novos} novos | total único: {len(final)}')
    return path, final


# ─────────────────────────────────────────────────────────────────────────────
# EXTRAÇÃO AUTOMÁTICA — fluxo [1] com pergunta pré-Edge
# ─────────────────────────────────────────────────────────────────────────────

def extrair_automatico() -> Tuple[list, Optional[str]]:
    """
    [1] Fluxo completo:
        Terminal → modalidade + concurso
        Edge → login → usuário escolhe SÓ a modalidade → SIM
        Script clica Detalhes e pagina até acabar (SEM filtro de estado/lotérica)
    """
    global SESSAO_AUTORIZADA, ROTULO_ARQUIVO, ROTULO_NOME

    mod_pre = _coletar_modalidade_pre_extracao()
    if mod_pre:
        ROTULO_ARQUIVO = mod_pre
        ROTULO_NOME = mod_pre.label

    concurso_pre = _coletar_concurso_pre_extracao()

    cfg_atual = FILTRO_LOTERICA if (
        FILTRO_LOTERICA and FILTRO_LOTERICA.qualquer_loterica
    ) else cfg_qualquer_loterica(None)

    _exibir_resumo_pre_extracao(mod_pre or ROTULO_ARQUIVO, concurso_pre, cfg_atual)

    if driver is None:
        if not iniciar_navegador():
            return [], None
    elif not _no_site_boloes():
        try:
            driver.get(URL_BOLOES)
            time.sleep(2)
        except Exception:
            pass
        _out('\n  Edge já aberto — faça login e escolha a modalidade se precisar.')

    SESSAO_AUTORIZADA = False
    _out('\n  ⏸⏸⏸  PAUSA ATIVA — digite SIM no terminal para liberar a extração.')
    if not aguardar_site_pronto():
        return [], None

    SESSAO_AUTORIZADA = True

    mod = ROTULO_ARQUIVO or _modalidade_extracao(driver)
    mod_slug = mod.slug if mod else 'boloes'
    parser_slug = mod.parser_slug if mod else ''
    concurso_final = concurso_pre
    cfg = cfg_atual

    arquivo_base = gerar_arquivo_base(cfg, mod, concurso_final)
    fila = estados_varredura('SP')
    imprimir_fila_estados('SP')

    ck = carregar_checkpoint(PASTA_JSON) or {}
    ufs_concluidas = [
        str(u).upper() for u in (ck.get('ufs_concluidas') or []) if str(u).strip()
    ]
    uf_retomar = ''
    pagina_retomar = 1
    if ck.get('status') in (STATUS_EXECUTANDO, STATUS_PAUSADO):
        uf_retomar = str(ck.get('uf_atual') or '').upper()
        pagina_retomar = int(ck.get('pagina_atual') or 0) + 1
        if pagina_retomar < 1:
            pagina_retomar = 1

    print('\n' + '=' * 60)
    print('  EXTRAÇÃO AUTOMÁTICA — LISTA DO SITE')
    print('=' * 60)
    print(f'  Modalidade : {mod.label if mod else "auto-detectar"}')
    print(f'  Concurso   : {concurso_final if concurso_final else "auto-detectar"}')
    print('  Filtro     : NENHUM (você só escolheu a modalidade)')
    print('  Ação       : clicar Detalhes → próxima página → até acabar')
    print(f'  Arquivo    : {arquivo_base}.json (gravado em tempo real)')
    if ufs_concluidas:
        print(f'  UFs já ok  : {", ".join(ufs_concluidas)}')
    if uf_retomar:
        print(f'  Retomada   : {uf_retomar} página {pagina_retomar}')
    print(LEGENDA_API)

    # forcar_pagina_inicial=None → _loop pergunta [C] Continuar / [N] Nova pela página
    boloes_final, _, painel, ab = _loop_extracao_paginas(
        cfg, parser_slug, mod_slug, arquivo_base,
        manual_paginas=False, rodada_filtro=1, mod_esperada=mod,
        concurso_alvo=concurso_final,
        forcar_pagina_inicial=None,
        uf_varredura='',
        ufs_concluidas=[],
        marcar_concluido_ao_fim=True,
    )
    arquivo_base = ab or arquivo_base

    if boloes_final:
        mod_final = extrair_modalidade_de_boloes(boloes_final) or mod
        ab = _renomear_json_sessao(arquivo_base, boloes_final, mod_final)
        _validar_modalidade_coerencia(mod_final, boloes_final)
        ufs = sorted({(b.get('uf') or '').upper() for b in boloes_final if b.get('uf')})
        if ufs:
            print(f'  UFs presentes no JSON (campo da lotérica): {", ".join(ufs)} ({len(ufs)})')

    # Som só no fim real da lista (última página)
    if painel.get('uf_concluida'):
        _aviso_sonoro_extracao_completa()
    else:
        ck_fim = carregar_checkpoint(PASTA_JSON) or {}
        if ck_fim.get('status') == STATUS_CONCLUIDO:
            _aviso_sonoro_extracao_completa()

    return boloes_final or [], ab

# ─────────────────────────────────────────────────────────────────────────────
# EXTRAÇÃO MANUAL — fluxo [2]
# ─────────────────────────────────────────────────────────────────────────────

def _resolver_cfg_filtro_rodada():
    _out('\n  Lendo filtro aplicado no site...')
    cfg = ler_filtro_aplicado_site(driver, _out)
    if cfg and (cfg.termo or cfg.codigo or cfg.qualquer_loterica):
        return cfg

    _out('\n' + '-' * 60)
    _out('  Filtro no site não lido automaticamente.')
    if FILTRO_LOTERICA and FILTRO_LOTERICA.qualquer_loterica:
        _out(f'  [ENTER] = qualquer lotérica + {FILTRO_LOTERICA.qtd_dezenas or 15} dezenas')
    elif FILTRO_LOTERICA and FILTRO_LOTERICA.termo:
        _out(f'  [ENTER] = usar config salva ({FILTRO_LOTERICA.termo})')
    _out('  * = qualquer + 15 dez | ou código/nome | X = menu')
    _out('-' * 60)
    try:
        resp = input('>>> ').strip()
    except EOFError:
        return None
    if not resp:
        if FILTRO_LOTERICA:
            return FILTRO_LOTERICA
        return cfg_qualquer_loterica(15)
    if resp.upper() == 'X':
        return None
    if resp in ('*', '-', 'todas', 'qualquer', 'QUALQUER', 'TODAS'):
        qtd = FILTRO_LOTERICA.qtd_dezenas if FILTRO_LOTERICA and FILTRO_LOTERICA.qtd_dezenas else 15
        return cfg_qualquer_loterica(qtd)
    codigo, nome = parse_termo_loterica(resp)
    qtd = FILTRO_LOTERICA.qtd_dezenas if FILTRO_LOTERICA else None
    return FiltroLotericaConfig(termo=resp, codigo=codigo, nome=nome, qtd_dezenas=qtd)


def extrair_sessao_multi_filtros() -> None:
    global SESSAO_AUTORIZADA

    print('\n' + '=' * 60)
    print('  MODO FILTRO MANUAL — MESMA SESSÃO LOGADA')
    print('=' * 60)
    print('\n  Login 1x → filtro no site → ENTER → baixa pág. 1')
    print('  Mesmo filtro: pág. 2, 3… → ENTER a cada página → FIM')
    print('  Novo filtro: aplique no site → ENTER → repete')
    print(LEGENDA_API)

    if driver is None:
        if not preparar_login_unico():
            return
    elif not _usuario_logado_caixa():
        if _no_site_boloes():
            print('\n  [AVISO] Login não confirmado, mas site de bolões aberto — continuando.')
        else:
            print('\n  Sessão expirada — faça login de novo.')
            if not preparar_login_unico():
                return

    mod_slug = ROTULO_ARQUIVO.slug if ROTULO_ARQUIVO else 'quina'
    parser_slug = ROTULO_ARQUIVO.parser_slug if ROTULO_ARQUIVO else 'quina'

    rodada = 1
    resumos_rodadas: list = []

    while True:
        if not aguardar_filtro_manual_pagina1(rodada=rodada):
            break

        cfg = _resolver_cfg_filtro_rodada()
        if not cfg or not (cfg.termo or cfg.codigo or cfg.qualquer_loterica):
            _out('\n  Rodada cancelada — voltando ao menu.')
            break

        mod = _modalidade_extracao(driver)
        mod_slug = mod.slug if mod else mod_slug
        parser_slug = mod.parser_slug if mod else parser_slug

        SESSAO_AUTORIZADA = True
        limpar_capturas_api(driver)
        arquivo_base = gerar_arquivo_base(cfg, mod)

        print(f'\n  Iniciando rodada {rodada}...')
        boloes, hashes, painel, arquivo_base = _loop_extracao_paginas(
            cfg, parser_slug, mod_slug, arquivo_base,
            manual_paginas=True, rodada_filtro=rodada,
            voce_encerra=True, mod_esperada=mod,
        )

        resumos_rodadas.append({
            'rodada': rodada,
            'loterica': cfg.termo,
            'dezenas': cfg.qtd_dezenas or 'qualquer',
            'registros': len(boloes),
            'arquivo': f'{arquivo_base}.json',
        })

        print('\n' + '-' * 60)
        print(f'  RODADA {rodada} CONCLUÍDA — {len(boloes)} reg. | filtro {cfg.termo}')
        print('-' * 60)

        try:
            resp = input('\n>>> Aplicar OUTRO filtro no site? [S/n] ').strip().lower()
        except EOFError:
            break
        if resp == 'n':
            break
        rodada += 1

    if resumos_rodadas:
        print('\n' + '=' * 60)
        print('  RESUMO — TODOS OS FILTROS DESTA SESSÃO')
        print('=' * 60)
        total = 0
        for r in resumos_rodadas:
            print(f"  Rodada {r['rodada']}: {r['registros']:>4} reg. | {r['loterica']} | {r['arquivo']}")
            total += r['registros']
        print(f'\n  Total: {total} reg. em {len(resumos_rodadas)} filtro(s).')
        print('=' * 60)


# ─────────────────────────────────────────────────────────────────────────────
# Configurações iniciais / menu
# ─────────────────────────────────────────────────────────────────────────────

def _carregar_config_inicio() -> bool:
    global FILTRO_LOTERICA, ROTULO_ARQUIVO, ROTULO_NOME
    cached = _carregar_config_cache()
    if not cached:
        return False
    FILTRO_LOTERICA, _mod_cache = cached
    ROTULO_ARQUIVO = None
    ROTULO_NOME = 'modalidade atual'
    return bool(FILTRO_LOTERICA)


def configurar_loterica() -> bool:
    global FILTRO_LOTERICA, ROTULO_ARQUIVO, ROTULO_NOME
    try:
        FILTRO_LOTERICA, ROTULO_ARQUIVO = ler_config_extracao()
        ROTULO_NOME = _rotulo_nome()
        if not FILTRO_LOTERICA or not (FILTRO_LOTERICA.termo or '').strip():
            print('\n>>> Lotérica inválida ou vazia.')
            FILTRO_LOTERICA = None
            return False
        print(f'\n>>> Config OK | Lotérica: {FILTRO_LOTERICA.termo} | Modalidade: {ROTULO_NOME}')
        return True
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        print(f'\n>>> ERRO: {exc}')
        traceback.print_exc()
        return False


def _menu_consolidar_capturas() -> None:
    from boloes_consolidar import consolidar_capturas_pasta

    mod_slug = ROTULO_ARQUIVO.slug if ROTULO_ARQUIVO else 'quina'
    path, total = consolidar_capturas_pasta(
        PASTA_CAPTURAS, PASTA_JSON, 'sem-concurso', mod_slug,
        FILTRO_LOTERICA.codigo if FILTRO_LOTERICA else None,
        FILTRO_LOTERICA.qtd_dezenas if FILTRO_LOTERICA else None,
    )
    print(f'\n>>> Consolidado a partir de capturas-api/: {path}')
    print(f'>>> Total único: {total}')


def menu_principal() -> None:
    global FILTRO_LOTERICA, ROTULO_ARQUIVO, ROTULO_NOME

    while True:
        try:
            print('\n' + '=' * 60)
            print('  EXTRATOR DE BOLOES — Caixa (API)')
            print(f'  >>> VERSAO: {VERSAO_EXTRATOR}')
            print('=' * 60)
            _imprimir_status_modalidade()
            _imprimir_tabela_modalidades_resumida()
            print(f'\n  JSON: {PASTA_JSON}')
            print('\n[1] EXTRAIR AUTOMATICO')
            print('    modalidade + concurso -> Edge -> login -> PAUSA -> digite SIM')
            print('    Clica Detalhes e pagina ate acabar | SEM filtro de estado')
            print('    Enter vazio NAO inicia')
            print('[2] EXTRAIR MANUAL (ENTER a cada pagina / varios filtros)')
            print('[3] Consolidar capturas-api/')
            print('[M] Tabela completa de modalidades')
            print('[0] Fechar navegador')
            print('-' * 60)
            print('  Opcional: M1-M9 | QSJ | DSP — so para forcar parser')
            print('-' * 60)

            opcao = input('Opção: ').strip().upper()

            if not opcao:
                continue
            if opcao.startswith('M') and len(opcao) == 2 and opcao[1].isdigit():
                if _trocar_modalidade_por_entrada(opcao[1]):
                    continue
            if opcao == 'M':
                imprimir_menu_modalidades()
                continue
            if opcao in TECLAS_ESPECIAIS:
                _trocar_modalidade_por_entrada(opcao)
                continue
            if opcao in ('4', '5', '6', '7', '8', '9'):
                if _trocar_modalidade_por_entrada(opcao):
                    continue
            if opcao not in ('0', '1', '2', '3', 'M'):
                mod_direto = resolver_modalidade_menu(opcao)
                if mod_direto:
                    _aplicar_modalidade(mod_direto)
                    continue
            if opcao == '1':
                extrair_automatico()
            elif opcao == '2':
                extrair_sessao_multi_filtros()
            elif opcao == '3':
                _menu_consolidar_capturas()
            elif opcao == '0':
                fechar_navegador()
                print('\n>>> Navegador fechado. CTRL+C para sair.')
            else:
                print('\n>>> Opção inválida.')

        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f'\n>>> ERRO: {exc}')
            traceback.print_exc()


def main() -> None:
    global FILTRO_LOTERICA, ROTULO_ARQUIVO, ROTULO_NOME
    print('\n' + '#' * 60, flush=True)
    print(f'  VERSAO DO EXTRATOR: {VERSAO_EXTRATOR}', flush=True)
    print('  Se NAO aparecer v3 acima, feche o terminal e abra de novo.', flush=True)
    print('#' * 60 + '\n', flush=True)
    _carregar_config_inicio()
    menu_principal()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n\nEncerrado pelo usuário (CTRL+C).')
    finally:
        fechar_navegador()
        print('Fim!')
