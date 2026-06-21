# -*- coding: utf-8 -*-
"""
Extrator de bolões via API (interceptação JSON) — Caixa.

Fluxo [1] AUTOMÁTICO (principal):
  1. Edge abre o site — faça LOGIN, modalidade, filtros e Aplicar
  2. Volte ao terminal e pressione ENTER
  3. Script extrai página 1, 2, 3… (Seguinte) até o botão desabilitar
  4. JSON em json-boloes/

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
from boloes_consolidar import consolidar_sessao, hashes_pagina, salvar_json_boloes
from boloes_filtro_loterica import (
    FiltroLotericaConfig,
    _carregar_config_cache,
    bolao_atende_filtro,
    bolao_corresponde_loterica,
    cfg_qualquer_loterica,
    eh_ultima_pagina,
    gerar_arquivo_base,
    garantir_sessao_caixa,
    ler_config_extracao,
    ler_filtro_aplicado_site,
    ler_modalidade_aplicada_site,
    parse_termo_loterica,
    aplicar_filtro_loterica,
    ir_proxima_pagina_lista,
    ir_para_pagina_lista,
    preparar_pagina_loterica,
    sessao_caixa_ativa,
    slug_loterica,
    tem_proxima_pagina,
    ultima_pagina_detectada,
)

CONFERENCIAS_BOLOES_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, '..'))
PASTA_JSON = os.path.join(CONFERENCIAS_BOLOES_DIR, 'json-boloes')
PASTA_CAPTURAS = os.path.join(CONFERENCIAS_BOLOES_DIR, 'capturas-api')
URL_BOLOES = 'https://www.loteriasonline.caixa.gov.br/silce-web/#/bolao-caixa'


MSG_ULTIMA_PAGINA = 'Última página — botão Seguinte desabilitado. Extração concluída.'

for _pasta in (CONFERENCIAS_BOLOES_DIR, PASTA_JSON, PASTA_CAPTURAS):
    os.makedirs(_pasta, exist_ok=True)

driver = None
FILTRO_LOTERICA: Optional[FiltroLotericaConfig] = None
ROTULO_ARQUIVO = None
ROTULO_NOME = 'modalidade atual'
SESSAO_AUTORIZADA = False


def _out(msg: str = '') -> None:
    """Print imediato no terminal (evita parecer travado apos ENTER)."""
    print(msg, flush=True)


def _driver_url(timeout: float = 6.0) -> str:
    """Le URL do Edge com timeout — evita travar se o navegador nao responder."""
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
        _out('  [AVISO] Edge nao respondeu a tempo — clique na janela do navegador e tente de novo.')
        return ''
    except Exception:
        return ''


def _no_site_boloes() -> bool:
    """Na area de boloes da Caixa (nao tela Keycloak). Verificacao rapida por URL."""
    url = _driver_url().lower()
    if not url:
        return driver is not None and sessao_caixa_ativa(driver)
    if any(x in url for x in ('login.caixa.gov.br', 'openid-connect', '/auth/realms/')):
        return False
    return 'loteriasonline.caixa.gov.br' in url or 'silce-web' in url


def _kb_arquivo(path: str) -> float:
    try:
        return os.path.getsize(path) / 1024 if os.path.isfile(path) else 0.0
    except OSError:
        return 0.0


def _path_json_sessao(arquivo_base: str) -> str:
    return os.path.join(PASTA_JSON, f'{arquivo_base}.json')


def _imprimir_linha_arquivo(arquivo_base: str, n_reg: int, *, pag: int = 0, pag_ok: int = 0, pag_meta: int = 0) -> None:
    """Uma linha: nome + reg. + KB (atualiza conforme baixa)."""
    path = _path_json_sessao(arquivo_base)
    kb = _kb_arquivo(path)
    nome = os.path.basename(path)
    if pag_ok and pag_meta:
        _out(f'  >> {nome} | pag {pag}: {pag_ok}/{pag_meta} | {n_reg} reg. | {kb:.1f} KB')
    else:
        _out(f'  >> {nome} | {n_reg} reg. | {kb:.1f} KB')


def _salvar_json_progresso(path: str, boloes: list) -> bool:
    """Grava JSON sem mensagens extras."""
    if not boloes:
        return False
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(boloes, f, ensure_ascii=False, indent=2)
    return True


def _montar_subset_progresso(
    boloes: list,
    driver,
    hashes: set,
    cfg,
    parser_slug: str,
    pagina: int,
    mod_esperada,
) -> list:
    """Junta paginas anteriores + boloes ja capturados nesta pagina (para save parcial)."""
    from boloes_api_caixa import coletar_boloes_das_capturas

    extra = coletar_boloes_das_capturas(
        driver, hashes, None, cfg, parser_slug, filtrar_dezenas=False,
    )
    for b in extra:
        b['pagina'] = pagina
    temp = list(boloes) + extra
    subset = _boloes_do_filtro(temp, cfg)
    subset, _ = _filtrar_boloes_modalidade(subset, mod_esperada)
    return subset


def _gravar_e_mostrar_progresso(
    boloes: list,
    driver,
    hashes: set,
    cfg,
    parser_slug: str,
    pagina: int,
    mod_esperada,
    arquivo_base: str,
    n_ok: int,
    meta: int,
) -> str:
    """Salva JSON e mostra uma linha de progresso (reg. + KB)."""
    subset = _montar_subset_progresso(
        boloes, driver, hashes, cfg, parser_slug, pagina, mod_esperada,
    )
    if subset:
        arquivo_base = _renomear_json_sessao(arquivo_base, subset, mod_esperada)
    path = _path_json_sessao(arquivo_base)
    if _salvar_json_progresso(path, subset):
        _imprimir_linha_arquivo(arquivo_base, len(subset), pag=pagina, pag_ok=n_ok, pag_meta=meta)
    return arquivo_base


def _salvar_bolao_individual(path: str, bolao: dict, cfg, mod_esperada) -> bool:
    """Adiciona um unico bolao ao arquivo JSON existente (append incremental).
    Se o arquivo nao existe, cria um novo com este bolao.
    Evita duplicatas por hash_bolao."""
    import hashlib
    existente = []
    if os.path.isfile(path):
        try:
            with open(path, encoding='utf-8') as f:
                existente = json.load(f) or []
        except Exception:
            existente = []
    # Checar duplicata por hash
    h_novo = hashlib.md5(json.dumps(bolao, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    for b in existente:
        h_ex = hashlib.md5(json.dumps(b, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
        if h_ex == h_novo:
            return False  # ja existe, nao duplica
    existente.append(bolao)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(existente, f, ensure_ascii=False, indent=2)
    return True


def _criar_arquivo_sessao_inicial(arquivo_base: str) -> str:
    """Cria JSON vazio na pasta — visivel no Explorer logo apos ENTER."""
    path = _path_json_sessao(arquivo_base)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.isfile(path):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump([], f, ensure_ascii=False)
    _out(f'  Arquivo: {path}')
    _imprimir_linha_arquivo(arquivo_base, 0)
    return path


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
    print('\n  ' + '-' * 56)
    print(f'  [PAINEL] Pagina {pagina} concluida')
    linha = f'    Nesta pagina : +{n_novos} registro(s) | {caps_pag} captura(s) API'
    if n_det:
        linha += f' | detalhes_tela={n_det}'
        if pend:
            linha += f' | faltam={pend}'
    print(linha)
    print(f'    Total sessao  : {len(boloes)} registro(s) | {len(hashes)} unico(s)')
    print(f'    Paginas       : {pagina} processada(s) | {pag_com} com dados | {pag_vaz} vazia(s)')
    if painel['capturas_api']:
        print(f'    Capturas API  : {painel["capturas_api"]} acumulada(s) na sessao')
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
    print('\n' + '=' * 60)
    print('  RESUMO FINAL DA EXTRACAO')
    print('=' * 60)
    print(f'\n  Lotérica alvo     : {cfg.termo or ("QUALQUER" if cfg.qualquer_loterica else "(filtro manual no site)")}')
    print(f'  Paginas processadas: {painel["paginas_processadas"]}')
    print(f'  Paginas com dados  : {painel["paginas_com_dados"]}')
    print(f'  Paginas vazias     : {painel["paginas_vazias"]}')
    print(f'  Registros capturados: {len(boloes)}')
    print(f'  Registros unicos   : {len(hashes)} (hash_bolao)')
    print(f'  Capturas API       : {painel["capturas_api"]} JSON(s)')
    if painel['descartados_loterica']:
        print(f'  Descartados        : {painel["descartados_loterica"]} (lotérica diferente)')
    if painel.get('descartados_modalidade'):
        print(f'  Descartados        : {painel["descartados_modalidade"]} (modalidade diferente)')
    print(f'  Tempo              : {tempo_seg // 60}min {tempo_seg % 60}s')
    print(f'\n  Arquivo sessao     : {path_sessao}')

    if painel['por_pagina']:
        print('\n  Registros por pagina:')
        for pg in sorted(painel['por_pagina']):
            n = painel['por_pagina'][pg]
            barra = '#' * min(n, 40) if n else '(vazia)'
            print(f'    Pag {pg:>3}: {n:>4}  {barra}')
    print('=' * 60)


def _rotulo_nome() -> str:
    return ROTULO_ARQUIVO.label if ROTULO_ARQUIVO else 'modalidade atual'


def _rotulo_modalidade_menu() -> str:
    """Ex.: [6] Dia de Sorte  ou  QSJ — Quina de São João"""
    if not ROTULO_ARQUIVO:
        return '(nao configurada)'
    m = ROTULO_ARQUIVO
    if getattr(m, 'especial', False) and m.tecla:
        return f'{m.tecla} — {m.label}'
    num = getattr(m, 'numero', None)
    if num and num <= 9:
        return f'[{num}] {m.label}'
    return m.label


def _imprimir_tabela_modalidades_resumida() -> None:
    """Tabela compacta — opcional, so se quiser forcar parser no terminal."""
    _out('\n  OPCIONAL — forcar parser no terminal (senao usa API do site):')
    _out('  M1 Mega-Sena   M2 Quina        M3 Lotofacil')
    _out('  M4 Lotomania   M5 Timemania    M6 Dia de Sorte')
    _out('  M7 Super Sete  M8 Dupla Sena   M9 +Milionaria')
    _out('  Especiais: DSP | QSJ | LTI | MSV | MS3')


def _imprimir_status_modalidade() -> None:
    if ROTULO_ARQUIVO:
        _out(f'\n  Parser terminal (opcional): {_rotulo_modalidade_menu()}')
    else:
        _out('\n  Modalidade: vem da API do site (MEGA_SENA, QUINA…) — nao precisa M1.')


def _aplicar_modalidade(mod) -> bool:
    """Define modalidade ativa e confirma no terminal."""
    global ROTULO_ARQUIVO, ROTULO_NOME
    if not mod:
        return False
    ROTULO_ARQUIVO = mod
    ROTULO_NOME = _rotulo_nome()
    _out(f'\n>>> Modalidade: {_rotulo_modalidade_menu()}')
    if getattr(mod, 'especial', False):
        _out(f'>>> Base: {mod.base_label} | Epoca: {mod.epoca}')
    _out(f'>>> Extrai: {mod.extracao}')
    _out('>>> Opcional: QSJ, 9, etc. ajustam só o parser do JSON.')
    return True


def _trocar_modalidade_por_entrada(entrada: str) -> bool:
    mod = resolver_modalidade_menu(entrada)
    if not mod:
        return False
    return _aplicar_modalidade(mod)


def iniciar_navegador() -> bool:
    global driver
    if driver is not None:
        return True
    try:
        _out('\nIniciando Edge (hook API)...')
        opts = webdriver.EdgeOptions()
        opts.page_load_strategy = 'eager'
        driver = webdriver.Edge(options=opts)
        driver.set_page_load_timeout(45)
        instalar_interceptador_api(driver)
        driver.get(URL_BOLOES)
        _out('Edge aberto — faca LOGIN no navegador.')
        _out('(Captura so comeca apos ENTER com sessao detectada.)')
        return True
    except Exception as exc:
        print(f'\n>>> ERRO ao abrir Edge: {exc}')
        traceback.print_exc()
        driver = None
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


def configurar_modalidade_apenas() -> bool:
    """Só modalidade — lotérica vem do filtro manual no site (modo [2])."""
    global ROTULO_ARQUIVO, ROTULO_NOME
    try:
        from boloes_modalidades import ler_modalidade_terminal
        ROTULO_ARQUIVO = ler_modalidade_terminal()
        ROTULO_NOME = _rotulo_nome()
        print(f'\n>>> Modalidade: {ROTULO_NOME}')
        print('>>> Modo [2]: lotérica e dezenas voce escolhe NO SITE a cada rodada.')
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
            print('\n>>> Lotérica invalida ou vazia. Tente de novo (ex.: 9833).')
            FILTRO_LOTERICA = None
            return False
        print(f'\n>>> Config OK | Lotérica: {FILTRO_LOTERICA.termo} | Modalidade: {ROTULO_NOME}')
        return True
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        print(f'\n>>> ERRO na configuracao: {exc}')
        traceback.print_exc()
        return False


def _exigir_config_extracao(acao: str = 'extrair') -> bool:
    """[1] exige lotérica OU modo qualquer lotérica — abre [9] se faltar."""
    if FILTRO_LOTERICA and (
        (FILTRO_LOTERICA.termo or '').strip()
        or FILTRO_LOTERICA.codigo
        or FILTRO_LOTERICA.qualquer_loterica
    ):
        return True

    print('\n' + '=' * 60)
    print('  FILTRO NAO CONFIGURADO')
    print('=' * 60)
    print(f'\n  Para {acao}, use [9]:')
    print('    · lotérica fixa (ex.: 9833), ou')
    print('    · * = QUALQUER lotérica + 15 dezenas (varredura SP / páginas)')
    print('  Abrindo configuracao agora (ou CTRL+C para cancelar)...\n')

    if configurar_loterica():
        return True

    print('\n>>> Sem filtro — use [9] no menu antes de [1].')
    print('>>> Modo [2]: filtre no site (estado SP + 15 dez., sem lotérica).')
    return False


def _exigir_modalidade(acao: str = 'extrair') -> bool:
    """[2] multi-filtro: só modalidade (lotérica vem do filtro manual no site)."""
    if ROTULO_ARQUIVO:
        return True
    print('\n' + '=' * 60)
    print('  MODALIDADE NAO CONFIGURADA')
    print('=' * 60)
    print(f'\n  Para {acao}, escolha a modalidade (ex.: QSJ = Quina de São João).')
    print('  Lotérica NAO precisa aqui — voce filtra no site a cada rodada.\n')
    if configurar_modalidade_apenas():
        return bool(ROTULO_ARQUIVO)
    return False


def _cfg_filtro_site() -> FiltroLotericaConfig:
    """Sem lotérica no terminal — só dezenas (usuário filtra estado no site)."""
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
    """Sessao autenticada — heuristica rapida (nao trava no DOM)."""
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
            if 'recuperar-dados' in url or 'dxn1yxjpb3' in url:
                if _payload_tem_usuario(cap.get('data')):
                    return True
    except Exception:
        pass
    return False


def aguardar_login_caixa() -> bool:
    """Modo [2]: pausa após login (sem captura)."""
    print('\n' + '=' * 60)
    print('  FACA LOGIN (script pausado)')
    print('=' * 60)
    print('\n1. No Edge: LOGIN na Caixa')
    print('2. Abra Boloes Caixa / lista de boloes')
    print('\n3. Volte aqui e pressione ENTER apos o login')

    while True:
        try:
            input('\n>>> ENTER apos LOGIN no site... ')
        except EOFError:
            return False

        if _usuario_logado_caixa() or _no_site_boloes():
            _out('\n  Login OK.')
            return True

        print('\n  >>> Ainda na tela de login. Faca login e tente de novo.')
        print('  (Script pausado — zero captura.)')


def aguardar_site_pronto() -> bool:
    """Um ENTER: login + modalidade + filtros no site — depois comeca a extracao."""
    print('\n' + '=' * 60)
    print('  PREPARE NO SITE — depois ENTER aqui')
    print('=' * 60)
    print('\n  1. LOGIN na Caixa')
    print('  2. Escolha a MODALIDADE')
    print('  3. Filtros (estado, dezenas, loterica…) + APLICAR — pagina 1')
    print('  4. Volte aqui e pressione ENTER')
    print('')
    print('  O script clica Seguinte sozinho ate desabilitar.')
    print(f'  JSON: {PASTA_JSON}')
    print('=' * 60)

    while True:
        try:
            input('\n>>> ENTER para iniciar a extracao... ')
        except EOFError:
            return False
        _out('\n  OK — iniciando extracao...')
        _out(f'  Pasta: {PASTA_JSON}')
        _out('  Em instantes o .json aparece ai (vazio no inicio, KB sobe depois).')
        return True


def aguardar_filtro_manual_pagina1(rodada: int = 1) -> bool:
    """Modo [2]: usuário aplica filtro no site (pág. 1) e pressiona ENTER."""
    print('\n' + '=' * 60)
    if rodada == 1:
        print('  FILTRO NO SITE — pagina 1')
    else:
        print(f'  FILTRO {rodada} — troque no site (mesma sessao logada)')
    print('=' * 60)
    if rodada == 1:
        print('\n  1. Configure no Edge → ENTER aqui')
        print('  2. Script baixa bolões do filtro visível')
        print('  3. Pag. 2+ → navegue no site → ENTER | FIM = acabou este filtro')
    else:
        print('\n  1. No site: ajuste filtro → pagina 1')
        print('  2. ENTER aqui | paginas seguintes: navegue + ENTER | FIM')

    while True:
        try:
            input(f'\n>>> ENTER apos filtro aplicado (rodada {rodada}, pagina 1)... ')
        except EOFError:
            return False
        _out('\n  OK — recebido! Verificando pagina de boloes...')
        if _no_site_boloes():
            _out(f'  URL: {_driver_url()}')
            return True
        url = _driver_url() or '(sem resposta do Edge)'
        _out(f'\n  >>> Nao esta na lista de boloes. URL atual: {url}')
        _out('  Abra Boloes Caixa no Edge do script, aplique filtro e tente de novo.')


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
    """Descarta bolões de modalidade diferente da escolhida no site/terminal."""
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


def _modalidade_extracao(driver=None):
    """Terminal (M1–M9) forçado, senão modalidade lida no site após Aplicar.
    Se falhar, pergunta no terminal para o usuario digitar manualmente."""
    if ROTULO_ARQUIVO:
        _out(f'  Parser terminal (forcado): {ROTULO_ARQUIVO.label}')
        return ROTULO_ARQUIVO
    if driver is not None:
        mod = ler_modalidade_aplicada_site(driver, _out)
        if mod:
            _out(f'  Modalidade detectada no site: {mod.label}')
            return mod
    # Fallback: perguntar no terminal
    _out('  Modalidade: nao detectada automaticamente no site.')
    _out('  Exemplos: M1 (Mega-Sena), M2 (Quina), M6 (Dia de Sorte), QSJ, DSP')
    try:
        raw = input('  Digite a modalidade (M1-M9 / QSJ / DSP / nome): ').strip()
    except EOFError:
        return None
    if raw:
        mod = resolver_modalidade_menu(raw)
        if mod:
            _out(f'  [OK] Modalidade: {mod.label}')
            return mod
    _out('  Modalidade invalida — extracao cancelada.')
    return None


def _validar_modalidade_coerencia(mod_esperada, boloes: list) -> None:
    """Compara site/terminal vs modalidade gravada no JSON."""
    if not boloes:
        return
    mod_json = extrair_modalidade_de_boloes(boloes)
    label_json = mod_json.label if mod_json else str(boloes[0].get('modalidade') or '?')
    label_site = mod_esperada.label if mod_esperada else '(nao definida)'
    concurso = extrair_concurso_de_boloes(boloes)

    if mod_esperada and mod_json and mod_esperada.slug != mod_json.slug:
        _out(
            f'\n  ERRO: Modalidade site/terminal ({label_site}) '
            f'difere da gravada no JSON ({label_json}).'
        )

    if mod_esperada and mod_json:
        arq_ok = nome_arquivo_consolidado_padrao(concurso, mod_esperada)
        arq_json = nome_arquivo_consolidado_padrao(concurso, mod_json)
        if arq_ok != arq_json:
            _out(
                f'  ERRO: Nome do arquivo ({arq_json}) nao bate com modalidade do site ({arq_ok}).'
            )
        else:
            _out(f'  OK modalidade: {label_site} | concurso {concurso} | {arq_ok}')


def _renomear_json_sessao(arquivo_base: str, boloes: list, mod) -> str:
    """Ajusta nome após 1ª página — boloes_{concurso}_{modalidade}.json"""
    if not boloes:
        return arquivo_base
    mod_json = extrair_modalidade_de_boloes(boloes)
    mod_use = mod or mod_json
    if mod and mod_json and mod.slug != mod_json.slug:
        _out(
            f'  [AVISO] Bolões extraídos são {mod_json.label}, mas site/terminal = {mod.label}. '
            f'Arquivo usa {mod.label}.'
        )
    novo = nome_arquivo_sessao(extrair_concurso_de_boloes(boloes), mod_use)
    if novo == arquivo_base:
        return arquivo_base
    antigo = os.path.join(PASTA_JSON, f'{arquivo_base}.json')
    destino = os.path.join(PASTA_JSON, f'{novo}.json')
    if os.path.isfile(antigo) and antigo != destino:
        if os.path.isfile(destino):
            existentes = []
            try:
                with open(destino, encoding='utf-8') as f:
                    existentes = json.load(f) or []
            except Exception:
                pass
            salvar_json_boloes(destino, existentes + boloes)
            os.remove(antigo)
        else:
            os.rename(antigo, destino)
        _out(f'  Arquivo renomeado: {os.path.basename(destino)}')
    return novo


def preparar_login_unico() -> bool:
    """Abre Edge + login. Mesma sessão para vários filtros manuais depois."""
    global SESSAO_AUTORIZADA
    SESSAO_AUTORIZADA = False
    if not iniciar_navegador():
        return False
    print('\n  Edge aberto — faca LOGIN (script aguarda, nada roda ainda).')
    if not aguardar_login_caixa():
        return False
    if not _usuario_logado_caixa():
        print('\n>>> Login nao confirmado. Extração cancelada.')
        return False
    print('\n  Sessao logada — pronta para configurar filtros no site.')
    return True


def salvar_parcial(boloes, arquivo_base, pagina: int = 0, *, quiet: bool = False):
    path = _path_json_sessao(arquivo_base)
    if not boloes:
        if not quiet and os.path.isfile(path):
            _imprimir_linha_arquivo(arquivo_base, 0, pag=pagina or 0)
        return path
    if salvar_json_boloes(path, boloes):
        if not quiet:
            _imprimir_linha_arquivo(arquivo_base, len(boloes), pag=pagina or 0)
    return path


def _capturas_da_rodada(rodada: int) -> list[str]:
    pat = os.path.join(PASTA_CAPTURAS, f'api_r{rodada}_p*.json')
    return sorted(glob.glob(pat))


def _recuperar_boloes_das_capturas(
    cfg: FiltroLotericaConfig,
    parser_slug: str,
    mod_slug: str,
    arquivo_base: str,
    rodada: int = 1,
) -> list:
    """Se a extração por cliques falhou, tenta montar bolões dos JSONs em capturas-api/."""
    from boloes_consolidar import boloes_de_capturas_api

    arquivos = _capturas_da_rodada(rodada)
    if not arquivos:
        arquivos = sorted(glob.glob(os.path.join(PASTA_CAPTURAS, 'api_r*_p*.json')))
    if not arquivos:
        return []

    brutos = boloes_de_capturas_api(arquivos, cfg.codigo if not cfg.qualquer_loterica else None, cfg.qtd_dezenas)
    boloes = _boloes_do_filtro(brutos, cfg)
    if not boloes and brutos:
        _out(f'  [RECUPERO] {len(brutos)} bolão(ões) na API, mas 0 passaram no filtro {cfg.termo or cfg.codigo}.')

    if boloes:
        path = os.path.join(PASTA_JSON, f'{arquivo_base}.json')
        salvar_json_boloes(path, boloes)
        mod_b = extrair_modalidade_de_boloes(boloes)
        path_cons, _, _ = consolidar_sessao(
            PASTA_JSON,
            extrair_concurso_de_boloes(boloes),
            mod_b.slug if mod_b else mod_slug,
            boloes,
        )
        _out(f'\n  [RECUPERO] {len(boloes)} bolão(ões) a partir de {len(arquivos)} captura(s) API.')
        _out(f'  Salvo: {path}')
    return boloes


def _diagnosticar_capturas_sem_filtro(cfg: FiltroLotericaConfig, parser_slug: str) -> None:
    """Mostra quantos bolões existem na API sem o filtro de lotérica."""
    if not driver:
        return
    from boloes_api_caixa import coletar_boloes_das_capturas

    todos = coletar_boloes_das_capturas(
        driver, set(), print, None, parser_slug, filtrar_dezenas=False,
    )
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


def _boloes_sem_dezenas(boloes: list) -> bool:
    if not boloes:
        return True
    for b in boloes:
        apostas = b.get('apostas') or []
        if not apostas:
            return True
        dez = apostas[0].get('dezenas') if apostas else None
        if not dez:
            return True
    return False


def _trocar_modalidade_rapida(tecla: str) -> bool:
    """Atalho DSP QSJ LTI MSV MS3 ou numero 1-9 no menu principal."""
    return _trocar_modalidade_por_entrada(tecla)


def _consolidar_e_resumir(boloes_sessao, mod_esperada):
    if not boloes_sessao:
        return None, []
    mod_json = extrair_modalidade_de_boloes(boloes_sessao) or mod_esperada
    concurso = extrair_concurso_de_boloes(boloes_sessao)
    mod_ref = mod_json or mod_esperada
    mod_slug = mod_ref.slug if mod_ref else 'boloes'
    _validar_modalidade_coerencia(mod_esperada, boloes_sessao)
    path, final, novos = consolidar_sessao(PASTA_JSON, concurso, mod_slug, boloes_sessao)
    print(f'\n  CONSOLIDADO: {path}')
    print(f'  Sessao: {len(boloes_sessao)} | +{novos} novos | total unico: {len(final)}')
    return path, final


def _capturar_pagina_atual(
    cfg, parser_slug, hashes, pagina, boloes, manual: bool, painel: dict,
    mod_esperada=None,
    arquivo_base: str = '',
) -> int:
    """Captura bolões da página atual. Retorna quantidade de novos válidos."""
    if not SESSAO_AUTORIZADA:
        print('  [SESSAO] Captura bloqueada — conclua login + filtro manual antes.')
        return -1
    if not garantir_sessao_caixa(driver, pagina, print):
        print('  [SESSAO] Extração interrompida — faca login e rode de novo.')
        return -1

    if pagina == 1 and painel.get('varredura_estados'):
        print(f'  [FILTRO] Pagina 1 — {painel.get("uf_varredura", "UF")} (filtro aplicado pelo script).')
    elif pagina == 1:
        print('  [FILTRO] Pagina 1 — filtro manual (mantém capturas da lista).')
        print('  [TELA] Procurando botoes Detalhes na pagina...')
        n_det = aguardar_detalhes_visiveis(driver, minimo=1, timeout=12, log_fn=print)
        if n_det:
            print(f'  [TELA] {n_det} botao(oes) Detalhes visiveis na pagina.')
        else:
            print('  [TELA] Nenhum botao Detalhes detectado — confira filtro no site.')
        time.sleep(0.8)
    else:
        meta_preservar = ler_metadados_paginacao_api(driver)
        if meta_preservar:
            painel['paginacao_api'] = meta_preservar
        limpar_capturas_api(driver)
        if not manual:
            print(f'  [PAGINA] Avancando para pagina {pagina} (Seguinte)...')
            if not ir_proxima_pagina_lista(driver, print):
                if cfg.termo:
                    print(f'  [PAGINA] Seguinte falhou — tentando lotérica {cfg.termo} + navegação...')
                    if not preparar_pagina_loterica(driver, cfg, pagina, print):
                        print('  [FILTRO] Falha ao preparar pagina.')
                        return -1
                elif ultima_pagina_detectada(driver) or eh_ultima_pagina(driver):
                    return -2
                else:
                    meta_nav = ler_metadados_paginacao_api(driver)
                    ultima = (meta_nav or {}).get('ultima_pagina') or 0
                    if pagina <= ultima and ir_para_pagina_lista(driver, pagina, print):
                        print(f'  [PAGINA] Navegou para pagina {pagina} (fallback Angular).')
                    elif ultima_pagina_detectada(driver) or eh_ultima_pagina(driver):
                        return -2
                    else:
                        print(
                            f'  [PAGINA] Seguinte falhou ao ir para pagina {pagina} '
                            f'(API: {meta_nav or "sem metadados"}).'
                        )
                        return -1
            time.sleep(1.2)
            n_det = aguardar_detalhes_visiveis(driver, minimo=1, timeout=12)
            if n_det:
                print(f'  [TELA] Pagina {pagina}: {n_det} botao(oes) Detalhes visiveis.')
            elif pagina >= 2 and cfg.termo:
                print(f'  [FILTRO] Lista vazia — reaplicando lotérica {cfg.termo}...')
                aplicar_filtro_loterica(driver, cfg, print, somente_loterica=True)
                for _ in range(pagina - 1):
                    if not ir_proxima_pagina_lista(driver, print):
                        break
                    time.sleep(0.8)
                aguardar_detalhes_visiveis(driver, minimo=1, timeout=10)
            elif pagina >= 2:
                print('  [PAGINA] Lista vazia — confira filtro manual ou navegacao.')
        else:
            print(f'  [FILTRO] Pagina {pagina} — modo manual (voce navegou).')

    aguardar_capturas_api(driver, minimo=1, timeout=12)

    preparar_pagina_para_detalhes(driver, log_fn=print)
    meta = detectar_detalhes_pagina(driver, cfg, 55, preparar=False, log_fn=print)
    n_esperado = meta['n_esperado']
    codigos = meta['codigos']

    if n_esperado:
        print(f'  [TELA] Meta desta pagina: {n_esperado} bolao(oes) (= botoes Detalhes no site).')
        print('  [TELA] Modo rapido: API/JS — SEM popup (modal oculto se precisar clicar)...')
    else:
        print('  [TELA] Nenhum Detalhes visivel — tentando lista API interceptada...')

    ultimo_ok_prog = 0
    arquivo_prog = arquivo_base
    if arquivo_base:
        painel['arquivo_base'] = arquivo_base

    def _on_progresso_detalhar(n_ok: int, meta: int) -> None:
        nonlocal ultimo_ok_prog, arquivo_prog
        if not arquivo_base or n_ok <= ultimo_prog:
            return
        ultimo_ok_prog = n_ok
        arquivo_prog = _gravar_e_mostrar_progresso(
            boloes, driver, hashes, cfg, parser_slug, pagina,
            mod_esperada, arquivo_prog, n_ok, meta,
        )
        painel['arquivo_base'] = arquivo_prog

    # Salvar cada bolao individualmente assim que e capturado (incremental)
    _boloes_salvos_pagina: list = []

    def _on_bolao_capturado(bolao: dict):
        """Chamado a cada bolao capturado — salva imediatamente no arquivo."""
        if not arquivo_base:
            return
        bolao['pagina'] = pagina
        path = _path_json_sessao(arquivo_base)
        if _salvar_bolao_individual(path, bolao, cfg, mod_esperada):
            _boloes_salvos_pagina.append(bolao)
            kb = _kb_arquivo(path)
            _out(f'  [+] {bolao.get("nome_loterica", "?")[:25]} | pag {pagina} | {kb:.1f} KB')

    novos = detalhar_pagina_ate_esperado(
        driver, cfg, parser_slug, hashes, n_esperado, codigos, print,
        on_progresso=_on_progresso_detalhar if arquivo_base else None,
        on_bolao=_on_bolao_capturado if arquivo_base else None,
    )

    if not novos and n_esperado == 0:
        from boloes_api_caixa import coletar_boloes_das_capturas
        novos = coletar_boloes_das_capturas(
            driver, hashes, print, cfg, parser_slug, filtrar_dezenas=True,
        )
        if novos:
            print(f'  [RECUPERO] {len(novos)} bolão(ões) via lista API (sem botões Detalhes).')

    n_caps = len(ler_capturas_api(driver))
    painel['capturas_ultima_pagina'] = n_caps
    painel['capturas_api'] += n_caps
    meta_pag = ler_metadados_paginacao_api(driver)
    if meta_pag:
        painel['paginacao_api'] = meta_pag
    painel['detalhes_tela_pagina'] = n_esperado
    antes_filtro = len(novos)
    novos = _boloes_do_filtro(novos, cfg)
    novos, desc_mod = _filtrar_boloes_modalidade(novos, mod_esperada)
    if desc_mod:
        painel['descartados_modalidade'] = painel.get('descartados_modalidade', 0) + desc_mod
        alvo = mod_esperada.label if mod_esperada else '?'
        print(f'  [FILTRO] {desc_mod} descartado(s) — modalidade diferente de {alvo}')
    painel['pendentes_pagina'] = max(0, n_esperado - len(novos)) if n_esperado else 0

    if n_esperado and len(novos) < n_esperado:
        print(
            f'  [AVISO] Pagina incompleta: {len(novos)}/{n_esperado} bolões '
            f'({painel["pendentes_pagina"]} Detalhes ainda sem JSON).'
        )

    descartados = antes_filtro - len(novos)
    if descartados:
        painel['descartados_loterica'] += descartados
        dez = f' | {cfg.qtd_dezenas} dez.' if cfg.qtd_dezenas else ''
        print(f'  [FILTRO] {descartados} descartado(s) — fora do filtro {cfg.termo}{dez}')

    if not novos and n_caps > 0:
        _diagnosticar_capturas_sem_filtro(cfg, parser_slug)

    for b in novos:
        b['pagina'] = pagina
        b['indice'] = len(boloes) + 1
        b['rodada_filtro'] = painel.get('rodada_filtro', 1)
        if painel.get('uf_varredura'):
            b['uf_varredura'] = painel['uf_varredura']
        boloes.append(b)

    return len(novos)


def _loop_extracao_paginas(
    cfg: FiltroLotericaConfig,
    parser_slug: str,
    mod_slug: str,
    arquivo_base: str,
    manual_paginas: bool,
    rodada_filtro: int = 1,
    voce_encerra: bool = False,
    painel_extra: Optional[dict] = None,
    mod_esperada=None,
) -> Tuple[list, set, dict, str]:
    """Baixa paginas do filtro atual. voce_encerra=True: digite FIM para parar (modo [2])."""
    boloes: list = []
    hashes: set = set()
    hashes_pagina_anterior: set = set()
    painel = _novo_painel_extracao()
    painel['rodada_filtro'] = rodada_filtro
    if painel_extra:
        painel.update(painel_extra)
    inicio = time.time()
    pagina = 1

    limpar_capturas_api(driver)
    _out('  [API] Capturas anteriores limpas — só dados desta extração.')
    painel['arquivo_base'] = arquivo_base

    dez = cfg.qtd_dezenas or 'qualquer'
    lot_txt = 'QUALQUER lotérica' if cfg.qualquer_loterica else (cfg.termo or '(filtro manual no site)')
    uf_txt = f' | UF: {painel.get("uf_varredura")}' if painel.get('uf_varredura') else ''
    print('\n  [PAINEL] Contadores: paginas | registros/pagina | total | unicos')
    print(f'  Filtro ativo: {lot_txt} | dezenas: {dez}{uf_txt}')
    _criar_arquivo_sessao_inicial(arquivo_base)
    mod_nome = mod_esperada.label if mod_esperada else 'modalidade'
    _out(f'  [MONITOR] Arquivo sera criado: boloes_{{concurso}}_{mod_nome.lower().replace(" ", "-")}.json')
    _out(f'  [MONITOR] Local: {PASTA_JSON}')
    if mod_esperada:
        print(f'  Modalidade alvo: {mod_esperada.label} — ignora bolões de outras modalidades.')
    if voce_encerra:
        print('  Cada filtro: pag.1 automatica apos ENTER | pag.2+ voce navega + ENTER')
        print('  FIM = encerra SOMENTE o filtro atual (nao o login nem a sessao)')

    while True:
        if manual_paginas and pagina > 1:
            try:
                resp = input(
                    f'\n>>> [{cfg.termo}] PAGINA {pagina} no site — '
                    f'navegue e ENTER | FIM=acabou este filtro: '
                ).strip().upper()
            except EOFError:
                break
            if resp == 'FIM':
                print('  Fim deste filtro (voce encerrou).')
                break

        print(f'\n>>> Processando PAGINA {pagina}...')
        n_novos = _capturar_pagina_atual(
            cfg, parser_slug, hashes, pagina, boloes, manual_paginas, painel, mod_esperada,
            arquivo_base=painel.get('arquivo_base', arquivo_base),
        )
        arquivo_base = painel.get('arquivo_base', arquivo_base)
        if n_novos == -2:
            print(f'\n  {MSG_ULTIMA_PAGINA}')
            break
        if n_novos < 0:
            if tem_proxima_pagina(driver):
                print('  [PAGINA] Erro na captura, mas Seguinte habilitado — tentando próxima...')
                pagina += 1
                continue
            if ultima_pagina_detectada(driver):
                print(f'\n  {MSG_ULTIMA_PAGINA}')
                break
            print('\n  Extração interrompida (sessao).')
            break

        page_boloes = _boloes_do_filtro(
            [b for b in boloes if b.get('pagina') == pagina], cfg,
        )
        h_pag = hashes_pagina(page_boloes)
        if n_novos and h_pag and h_pag == hashes_pagina_anterior:
            print('  [AVISO] Pagina igual a anterior — confira navegacao.')
        hashes_pagina_anterior = h_pag

        _imprimir_painel_pagina(pagina, len(page_boloes), boloes, hashes, painel)

        if len(page_boloes) == 0:
            print(f'  Capturas API:\n{resumo_capturas(driver)}')
            dbg = os.path.join(PASTA_CAPTURAS, f'api_r{rodada_filtro}_p{pagina}_{int(time.time())}.json')
            salvar_capturas_brutas(driver, dbg)
            print(f'  Debug: {dbg}')

        subset = _boloes_do_filtro(boloes, cfg)
        subset, desc_mod = _filtrar_boloes_modalidade(subset, mod_esperada)
        if desc_mod:
            painel['descartados_modalidade'] = painel.get('descartados_modalidade', 0) + desc_mod

        if pagina == 1 and mod_esperada and subset:
            mod_json = extrair_modalidade_de_boloes(subset)
            if mod_json and mod_json.slug != mod_esperada.slug:
                _out(
                    f'\n  ERRO: Site = {mod_esperada.label}, mas bolões extraídos = {mod_json.label}.'
                )
                _out('  Extração interrompida — troque modalidade/filtro no site e tente de novo.')
                break

        if subset:
            arquivo_base = _renomear_json_sessao(arquivo_base, subset, mod_esperada)
            painel['arquivo_base'] = arquivo_base
            # Mostra o arquivo com nome correto (boloes_CONCURSO_MODALIDADE.json)
            path_final = os.path.join(PASTA_JSON, f'{arquivo_base}.json')
            kb = _kb_arquivo(path_final)
            _out(f'  [ARQUIVO] {os.path.basename(path_final)} | pag {pagina} | {len(subset)} reg | {kb:.1f} KB')

        salvar_parcial(subset, arquivo_base, pagina, quiet=True)
        if subset:
            _consolidar_e_resumir(subset, mod_esperada)

        if voce_encerra:
            print(
                f'\n  [FILTRO] Pagina {pagina} concluida ({len(page_boloes)} reg. nesta pag.). '
                f'Total deste filtro: {len(subset)} reg.'
            )
            print(
                f'  Proxima pagina deste filtro? Va para pag. {pagina + 1} no site e ENTER.'
            )
            print('  Era a ultima pagina? Na proxima pergunta digite FIM.')

        if not voce_encerra:
            meta_pag = ler_metadados_paginacao_api(driver) or painel.get('paginacao_api')
            if meta_pag:
                pa = int(meta_pag.get('pagina_atual') or pagina)
                up = int(meta_pag.get('ultima_pagina') or pagina)
                print(
                    f'  [PAGINA] API (info): pagina {pa} de {up} '
                    f'({meta_pag.get("total_registros", "?")} bolões).'
                )
            if tem_proxima_pagina(driver):
                pagina += 1
                continue
            if ultima_pagina_detectada(driver):
                print(f'\n  {MSG_ULTIMA_PAGINA}')
                break
            print('  [PAGINA] Seguinte não visível — aguardando e conferindo de novo...')
            time.sleep(2.0)
            if tem_proxima_pagina(driver):
                pagina += 1
                continue
            if ultima_pagina_detectada(driver):
                print(f'\n  {MSG_ULTIMA_PAGINA}')
                break
            print('  [AVISO] Paginação indeterminada — tentando avançar mesmo assim.')

        pagina += 1

    tempo = int(time.time() - inicio)
    subset_final = _boloes_do_filtro(boloes, cfg)
    subset_final, _ = _filtrar_boloes_modalidade(subset_final, mod_esperada)
    if not subset_final:
        recuperados = _recuperar_boloes_das_capturas(
            cfg, parser_slug, mod_slug, arquivo_base, rodada_filtro,
        )
        if recuperados:
            subset_final = _boloes_do_filtro(recuperados, cfg) or recuperados
            boloes = recuperados
    _imprimir_resumo_final(subset_final, hashes, painel, arquivo_base, cfg, tempo)
    if subset_final:
        _out(f'\n  Arquivo final: {os.path.join(PASTA_JSON, f"{arquivo_base}.json")}')
    elif painel.get('capturas_api', 0) > 0:
        _out('\n  [AVISO] Extração vazia apesar de capturas API — veja [DIAG] acima.')
        _out('  Confira: modalidade no site (QSJ = Quina de São João), lotérica e filtro de dezenas.')
    return subset_final, hashes, painel, arquivo_base


def _salvar_boloes_incremental(
    novos_boloes: list,
    arquivo_base: str,
    cfg,
    mod_esperada,
) -> int:
    """Salva cada bolao individualmente no arquivo JSON (append).
    Retorna quantos foram adicionados (nao duplicatas)."""
    if not novos_boloes:
        return 0
    path = _path_json_sessao(arquivo_base)
    added = 0
    for b in novos_boloes:
        if _salvar_bolao_individual(path, b, cfg, mod_esperada):
            added += 1
    if added:
        kb = _kb_arquivo(path)
        total = 0
        try:
            with open(path, encoding='utf-8') as f:
                total = len(json.load(f) or [])
        except Exception:
            pass
        _out(f'  [INCREMENTAL] +{added} bolao(oes) salvo(s) | total: {total} | {kb:.1f} KB')
    return added


def _perguntar_concurso(mod_label: str) -> str:
    """Pergunta o concurso ao usuario. ENTER = detecta automaticamente.
    O nome do arquivo sera: boloes_{concurso}_{modalidade}.json
    Exemplo: boloes_3020_mega-sena.json"""
    _out(f'\n  Modalidade detectada: {mod_label}')
    _out('  O nome do arquivo sera: boloes_{concurso}_{modalidade}.json')
    _out('  Exemplo: boloes_3020_mega-sena.json')
    _out('  Digite o numero do concurso (ex.: 3020) ou ENTER para detectar automaticamente:')
    try:
        resp = input('  Concurso: ').strip()
    except EOFError:
        return ''
    if resp:
        # So digitos
        digits = re.sub(r'\D', '', resp)
        if digits:
            _out(f'  [OK] Concurso informado: {digits}')
            _out(f'  [OK] Arquivo: boloes_{digits}_{mod_label.lower().replace(" ", "-")}.json')
            return digits
    _out('  Concurso sera detectado automaticamente dos boloes.')
    return ''

def extrair_automatico() -> Tuple[list, Optional[str]]:
    """[1] Edge abre o site -> voce prepara tudo -> ENTER -> extrai ate Seguinte desabilitar."""
    global SESSAO_AUTORIZADA

    if driver is None:
        if not iniciar_navegador():
            return [], None
    elif not _no_site_boloes():
        try:
            driver.get(URL_BOLOES)
            time.sleep(2)
        except Exception:
            pass

    SESSAO_AUTORIZADA = False
    if not aguardar_site_pronto():
        return [], None

    SESSAO_AUTORIZADA = True

    _out('\n  Lendo filtros e modalidade do site (apos seu Aplicar)...')
    cfg = ler_filtro_aplicado_site(driver, _out)
    if not cfg:
        _out('  [AVISO] Filtro não lido — usando modalidade do site + todas lotéricas.')
        cfg = _cfg_filtro_site()
    elif cfg.qualquer_loterica:
        _out('  [OK] Todas lotéricas / todos estados — só filtra pela modalidade escolhida.')

    mod = _modalidade_extracao(driver)
    if not mod:
        _out('\n  >>> EXTRAÇÃO CANCELADA: modalidade não detectada.')
        _out('  No site: clique no card Dia de Sorte, filtre Aldeota, Aplicar, ENTER aqui.')
        _out('  Ou forçe antes do [1]: M6 (Dia de Sorte) no terminal.')
        return [], None

    lot_txt = 'QUALQUER' if cfg.qualquer_loterica else (cfg.termo or '(manual)')
    _out(f'\n  === CONFIRMADO ===')
    _out(f'  Loterica alvo : {lot_txt}')
    _out(f'  Modalidade    : {mod.label}')
    _out(f'  Dezenas filtro: {cfg.qtd_dezenas or "qualquer"}')

    # Pergunta o concurso para montar o nome do arquivo ANTES de extrair
    concurso_digitado = _perguntar_concurso(mod.label)

    mod_slug = mod.slug if mod else 'boloes'
    parser_slug = mod.parser_slug if mod else ''
    arquivo_base = gerar_arquivo_base(cfg, mod, concurso_digitado)

    print('\n' + '=' * 60)
    print('  EXTRACAO AUTOMATICA')
    print('=' * 60)
    print(f'  Arquivo: {arquivo_base}.json')
    print('  Pagina 1 = filtro que voce aplicou no site')
    print('  Paginas 2, 3… = Seguinte automatico ate botao desabilitar')
    print(f'  Salvamento: incremental (cada bolao salvo imediatamente)')
    print(LEGENDA_API)

    boloes, _, _, ab = _loop_extracao_paginas(
        cfg, parser_slug, mod_slug, arquivo_base, manual_paginas=False, mod_esperada=mod,
    )
    if boloes:
        mod_final = extrair_modalidade_de_boloes(boloes) or mod
        ab = _renomear_json_sessao(ab, boloes, mod_final)
        _validar_modalidade_coerencia(mod_final, boloes)
    return boloes, ab


def _carregar_config_inicio() -> bool:
    """Carrega loterica salva — modalidade NAO vem do cache (evita Dia de Sorte fantasma)."""
    global FILTRO_LOTERICA, ROTULO_ARQUIVO, ROTULO_NOME
    cached = _carregar_config_cache()
    if not cached:
        return False
    FILTRO_LOTERICA, _mod_cache = cached
    ROTULO_ARQUIVO = None
    ROTULO_NOME = 'modalidade atual'
    return bool(FILTRO_LOTERICA)


def _resolver_cfg_filtro_rodada() -> Optional[FiltroLotericaConfig]:
    """Le filtro do site; se falhar, usa config do terminal ou digitacao manual."""
    _out('\n  Lendo filtro aplicado no site...')
    cfg = ler_filtro_aplicado_site(driver, _out)
    if cfg and (cfg.termo or cfg.codigo or cfg.qualquer_loterica):
        return cfg

    _out('\n' + '-' * 60)
    _out('  Filtro no site nao lido automaticamente.')
    if FILTRO_LOTERICA and FILTRO_LOTERICA.qualquer_loterica:
        _out(f'  [ENTER] = qualquer lotérica + {FILTRO_LOTERICA.qtd_dezenas or 15} dezenas (config salva)')
    elif FILTRO_LOTERICA and FILTRO_LOTERICA.termo:
        _out(f'  [ENTER] = usar config salva ({FILTRO_LOTERICA.termo})')
    _out('  * = qualquer lotérica + 15 dezenas | ou codigo/nome | X = menu')
    _out('-' * 60)
    try:
        resp = input('>>> ').strip()
    except EOFError:
        return None
    if not resp:
        if FILTRO_LOTERICA:
            if FILTRO_LOTERICA.qualquer_loterica or FILTRO_LOTERICA.qtd_dezenas:
                _out('  [FILTRO] Qualquer lotérica (config salva).')
                return FILTRO_LOTERICA
            if FILTRO_LOTERICA.termo:
                _out(f'  [FILTRO] Usando config: {FILTRO_LOTERICA.termo}')
                return FILTRO_LOTERICA
        qtd = FILTRO_LOTERICA.qtd_dezenas if FILTRO_LOTERICA and FILTRO_LOTERICA.qtd_dezenas else 15
        _out(f'  [FILTRO] Qualquer lotérica + {qtd} dezenas (padrao).')
        return cfg_qualquer_loterica(qtd)
    if resp.upper() == 'X':
        return None
    if resp in ('*', '-', 'todas', 'qualquer', 'QUALQUER', 'TODAS'):
        qtd = FILTRO_LOTERICA.qtd_dezenas if FILTRO_LOTERICA and FILTRO_LOTERICA.qtd_dezenas else 15
        return cfg_qualquer_loterica(qtd)
    if resp:
        codigo, nome = parse_termo_loterica(resp)
        _out(f'  [FILTRO] Usando loterica informada: {resp}')
        qtd = FILTRO_LOTERICA.qtd_dezenas if FILTRO_LOTERICA else None
        return FiltroLotericaConfig(termo=resp, codigo=codigo, nome=nome, qtd_dezenas=qtd)
    return None


def extrair_sessao_multi_filtros() -> None:
    """
    Mesma sessao logada:
    - Voce aplica filtro no site → ENTER → script detecta filtro e baixa pagina 1
    - Mesmo filtro: paginas 2, 3, 4, 5… — voce navega no site → ENTER a cada uma
    - FIM encerra o filtro atual; troca filtro no site → ENTER → comeca pag. 1 de novo
    """
    global SESSAO_AUTORIZADA

    print('\n' + '=' * 60)
    print('  MODO FILTRO MANUAL — MESMA SESSAO LOGADA')
    print('=' * 60)
    print('\n  Login 1x → filtro no site → ENTER → baixa pag. 1')
    print('  Mesmo filtro: pag. 2, 3, 4, 5… → ENTER a cada pagina → FIM')
    print('  Novo filtro: aplique no site (pag. 1) → ENTER → repete')
    print(LEGENDA_API)

    if driver is None:
        if not preparar_login_unico():
            return
    elif not _usuario_logado_caixa():
        if _no_site_boloes():
            print('\n  [AVISO] Login nao confirmado pela API, mas site de boloes aberto — continuando.')
        else:
            print('\n  Sessao expirada — faca login de novo.')
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

        if cfg.qualquer_loterica:
            dez = cfg.qtd_dezenas or 15
            _out(f'\n  Modo: QUALQUER lotérica | somente {dez} dezenas por aposta')

        mod = _modalidade_extracao(driver)
        mod_slug = mod.slug if mod else mod_slug
        parser_slug = mod.parser_slug if mod else parser_slug

        SESSAO_AUTORIZADA = True
        limpar_capturas_api(driver)

        # Pergunta o concurso (mesma logica do modo automatico)
        if rodada == 1:
            concurso_digitado = _perguntar_concurso(mod.label if mod else 'desconhecida')
        else:
            concurso_digitado = ''
        arquivo_base = gerar_arquivo_base(cfg, mod, concurso_digitado)

        print(f'\n  Iniciando rodada {rodada} — somente filtro detectado acima.')
        boloes, hashes, painel, arquivo_base = _loop_extracao_paginas(
            cfg, parser_slug, mod_slug, arquivo_base,
            manual_paginas=True,
            rodada_filtro=rodada,
            voce_encerra=True,
            mod_esperada=mod,
        )

        resumos_rodadas.append({
            'rodada': rodada,
            'loterica': cfg.termo,
            'dezenas': cfg.qtd_dezenas or 'qualquer',
            'registros': len(boloes),
            'arquivo': f'{arquivo_base}.json',
        })

        print('\n' + '-' * 60)
        print(
            f'  RODADA {rodada} CONCLUIDA — {len(boloes)} registro(s) | '
            f'filtro {cfg.termo} | dez. {cfg.qtd_dezenas or "qualquer"}'
        )
        print('-' * 60)

        try:
            resp = input('\n>>> Aplicar OUTRO filtro no site? [S/n] ').strip().lower()
        except EOFError:
            break
        if resp == 'n':
            break
        rodada += 1
        print('\n  Mesma sessao — novo filtro no site, comecando pela pagina 1.')

    if resumos_rodadas:
        print('\n' + '=' * 60)
        print('  RESUMO — TODOS OS FILTROS DESTA SESSAO')
        print('=' * 60)
        total = 0
        for r in resumos_rodadas:
            print(
                f"  Rodada {r['rodada']}: {r['registros']:>4} reg. | "
                f"{r['loterica']} | dez.{r['dezenas']} | {r['arquivo']}"
            )
            total += r['registros']
        print(f'\n  Total: {total} registro(s) em {len(resumos_rodadas)} filtro(s).')
        print('=' * 60)
    else:
        print('\n  Nenhum filtro concluido.')


def menu_principal() -> None:
    global FILTRO_LOTERICA, ROTULO_ARQUIVO, ROTULO_NOME

    while True:
        try:
            print('\n' + '=' * 60)
            print('  EXTRATOR DE BOLOES — Caixa (API)')
            print('=' * 60)
            _imprimir_status_modalidade()
            _imprimir_tabela_modalidades_resumida()
            print(f'\n  JSON: {PASTA_JSON}')
            print('  Arquivo: boloes_{concurso}_{modalidade}_CONSOLIDADO.json')
            print('\n[1] EXTRAIR AUTOMATICO')
            print('    -> Edge abre -> login + modalidade + filtros NO SITE')
            print('    -> ENTER aqui -> Seguinte ate desabilitar -> JSON em json-boloes/')
            print('[2] EXTRAIR MANUAL (ENTER a cada pagina / varios filtros)')
            print('[3] Consolidar capturas-api/')
            print('[M] Tabela completa de modalidades')
            print('[0] Fechar navegador')
            print('-' * 60)
            print('  Opcional: M1-M9 | QSJ | DSP — so se quiser forcar parser')
            print('-' * 60)

            opcao = input('Opcao: ').strip().upper()

            if not opcao:
                continue
            if opcao.startswith('M') and len(opcao) == 2 and opcao[1].isdigit():
                if _trocar_modalidade_rapida(opcao[1]):
                    continue
            if opcao == 'M':
                imprimir_menu_modalidades()
                continue
            if opcao in TECLAS_ESPECIAIS:
                _trocar_modalidade_rapida(opcao)
                continue
            if opcao in ('4', '5', '6', '7', '8', '9'):
                if _trocar_modalidade_rapida(opcao):
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
                print('\n>>> Navegador fechado. Press CTRL+C to quit')
            else:
                print('\n>>> Opcao invalida.')

        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f'\n>>> ERRO: {exc}')
            traceback.print_exc()


def _menu_consolidar_capturas() -> None:
    from boloes_consolidar import consolidar_capturas_pasta

    mod_slug = ROTULO_ARQUIVO.slug if ROTULO_ARQUIVO else 'quina'
    path, total = consolidar_capturas_pasta(
        PASTA_CAPTURAS,
        PASTA_JSON,
        'sem-concurso',
        mod_slug,
        FILTRO_LOTERICA.codigo if FILTRO_LOTERICA else None,
        FILTRO_LOTERICA.qtd_dezenas if FILTRO_LOTERICA else None,
    )
    print(f'\n>>> Consolidado a partir de capturas-api/: {path}')
    print(f'>>> Total unico: {total}')


def main() -> None:
    global FILTRO_LOTERICA, ROTULO_ARQUIVO, ROTULO_NOME

    _carregar_config_inicio()
    menu_principal()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n\nEncerrado pelo usuario (CTRL+C).')
    finally:
        fechar_navegador()
        print('Fim!')
