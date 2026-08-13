# -*- coding: utf-8 -*-
"""
Orquestrador [C] — ciclo AUTOMÁTICO pelas cards de #/inicio-bolao.

Fluxo:
  1) Login 1×
  2) Abre inicio-bolao e LÊ os cards (modalidade + concurso)
  3) PAUSA — digite SIM (pode mexer no Edge; nada baixa antes)
  4) Clica "Compre seu bolão" em cada card e extrai até o fim
  5) Grava 1 JSON por modalidade — sem misturar
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from boloes_cards_inicio import (
    URL_INICIO_BOLAO,
    abrir_lista_do_card,
    ir_para_inicio_bolao,
    listar_cards_modalidades,
)
from boloes_checkpoint_ciclo import (
    STATUS_AGUARDANDO,
    STATUS_ANDAMENTO,
    STATUS_CICLO_OK,
    STATUS_CICLO_PAUSA,
    STATUS_CICLO_EXEC,
    STATUS_CONCLUIDO,
    STATUS_ERRO,
    STATUS_PAUSADO,
    STATUS_PENDENTE,
    STATUS_VAZIO,
    atualizar_modalidade,
    atualizar_uf,
    calcular_resumo,
    modalidade_concluida_pelas_ufs,
    novo_ciclo,
    perguntar_retomada_ciclo,
    salvar_ciclo,
    set_operacao,
)
from boloes_modalidades import MODALIDADES_MENU, modalidade_por_slug

UNIDADE_LISTA = 'LISTA'
LogFn = Callable[[str], None]


def _barra(pct: float, largura: int = 20) -> str:
    pct = max(0.0, min(100.0, float(pct)))
    cheios = int(round(largura * pct / 100.0))
    return '█' * cheios + '░' * (largura - cheios)


def _ufs_dos_boloes(boloes: list) -> List[str]:
    return sorted({
        str(b.get('uf') or '').upper()
        for b in (boloes or [])
        if str(b.get('uf') or '').strip()
    })


def imprimir_painel_ciclo(ciclo: Dict[str, Any], out_fn: LogFn = print, ordem_slugs: Optional[List[str]] = None) -> None:
    res = ciclo.get('resumo') or calcular_resumo(ciclo)
    mods = ciclo.get('modalidades') or {}
    total = len(mods) or 1
    feitos = sum(
        1 for m in mods.values()
        if str(m.get('status') or '') in (STATUS_CONCLUIDO, STATUS_VAZIO, STATUS_ERRO)
    )
    pct = 100.0 * feitos / total

    out_fn('')
    out_fn('=' * 64)
    out_fn('  CICLO COMPLETO — AUTOMÁTICO (cards inicio-bolao)')
    out_fn('  1 JSON por modalidade | sem filtro UF | SEM POPUP')
    out_fn('=' * 64)
    out_fn(f'  Status     : {ciclo.get("status")}')
    out_fn(f'  Modalidade : {ciclo.get("modalidade_atual") or "—"}')
    out_fn(f'  Operação   : {ciclo.get("operacao") or "—"}')
    out_fn(f'  Progresso  : {_barra(pct)} {pct:.0f}%  ({feitos}/{total} modalidades)')
    out_fn(f'  Bolões≈    : {res.get("boloes_total", 0)} | Erros: {res.get("erros", 0)}')
    out_fn('-' * 64)
    slugs = list(ordem_slugs) if ordem_slugs else list(mods.keys())
    for slug in slugs:
        if slug not in mods:
            continue
        mod = mods[slug]
        st = str(mod.get('status') or STATUS_AGUARDANDO)
        label = mod.get('label') or slug
        lista = (mod.get('ufs') or {}).get(UNIDADE_LISTA) or {}
        pag = int(lista.get('pagina_atual') or 0)
        n = int(lista.get('boloes') or 0)
        conc = mod.get('concurso') or ''
        ufs_txt = mod.get('ufs_encontradas') or ''
        extra = f'pág {pag} | {n} bolões'
        if conc:
            extra += f' | conc {conc}'
        if ufs_txt:
            extra += f' | UFs: {ufs_txt}'
        out_fn(f'  {label:<28} {st:<14} {extra}')
    out_fn('=' * 64)


def imprimir_resumo_final(ciclo: Dict[str, Any], out_fn: LogFn = print) -> None:
    res = calcular_resumo(ciclo)
    inicio = ciclo.get('inicio') or ''
    fim = ciclo.get('fim') or ''
    out_fn('')
    out_fn('=' * 64)
    out_fn('  EXTRAÇÃO COMPLETA — RESUMO')
    out_fn('=' * 64)
    out_fn(f'  Início : {inicio}')
    out_fn(f'  Fim    : {fim}')
    if inicio and fim:
        try:
            t0 = datetime.strptime(inicio, '%Y-%m-%d %H:%M:%S')
            t1 = datetime.strptime(fim, '%Y-%m-%d %H:%M:%S')
            secs = int((t1 - t0).total_seconds())
            h, rem = divmod(secs, 3600)
            m, s = divmod(rem, 60)
            out_fn(f'  Duração: {h:02d}:{m:02d}:{s:02d}')
        except Exception:
            pass
    out_fn(f'  Modalidades concluídas : {res.get("modalidades_ok")}')
    out_fn(f'  Bolões                 : {res.get("boloes_total")}')
    out_fn(f'  Erros / pendências     : {res.get("erros")} / {res.get("ufs_pendente")}')
    out_fn('=' * 64)


def _garantir_unidade_lista(ciclo: Dict[str, Any], slug: str, label: str) -> None:
    mods = ciclo.setdefault('modalidades', {})
    if slug not in mods:
        mods[slug] = {
            'label': label,
            'status': STATUS_AGUARDANDO,
            'concurso': '',
            'arquivo_base': '',
            'ufs': {},
            'ufs_encontradas': '',
            'inicio': '',
            'fim': '',
        }
    else:
        mods[slug]['label'] = label or mods[slug].get('label') or slug
    ufs = mods[slug].setdefault('ufs', {})
    for k in list(ufs.keys()):
        if k != UNIDADE_LISTA:
            ufs.pop(k, None)
    if UNIDADE_LISTA not in ufs:
        ufs[UNIDADE_LISTA] = {
            'status': STATUS_AGUARDANDO,
            'pagina_atual': 0,
            'total_paginas': 0,
            'boloes': 0,
            'tentativas': 0,
            'erro': '',
            'atualizado_em': '',
        }


def _mod_de_card(card: dict):
    mod = card.get('mod')
    if mod is not None:
        return mod
    return modalidade_por_slug(str(card.get('slug') or ''))


def executar_ciclo_completo(
    api,
    *,
    modalidades_slugs: Optional[List[str]] = None,
    out_fn: LogFn = print,
) -> Dict[str, Any]:
    """
    Login 1× → lê cards → SIM 1× → para cada card abre lista e extrai até o fim.
    """
    pasta = api.PASTA_JSON
    wanted = None
    if modalidades_slugs:
        wanted = {s.strip().lower() for s in modalidades_slugs if s and str(s).strip()}

    continuar, ciclo_existente = perguntar_retomada_ciclo(pasta, out_fn=out_fn)

    if continuar and ciclo_existente:
        ciclo = ciclo_existente
        ciclo['status'] = STATUS_CICLO_EXEC
    else:
        out_fn('')
        out_fn('  CICLO AUTOMÁTICO:')
        out_fn('  — App abre inicio-bolao e lê os cards (modalidade + concurso)')
        out_fn('  — Mostra a lista e PAUSA — você digita SIM quando quiser')
        out_fn('  — Enquanto espera, pode clicar no Edge normalmente')
        out_fn('  — Depois do SIM: Compre seu bolão sozinho | 1 JSON/mod')
        # Placeholder; modalidades reais vêm dos cards após login
        seed = list(MODALIDADES_MENU)
        ciclo = novo_ciclo(
            [{'slug': m.slug, 'label': m.label} for m in seed],
            [UNIDADE_LISTA],
        )
        ciclo['modo'] = 'cards-auto'
        ciclo['concurso_inicial'] = ''

    salvar_ciclo(pasta, ciclo)

    if getattr(api, 'driver', None) is None:
        if not api.iniciar_navegador():
            ciclo['status'] = STATUS_CICLO_PAUSA
            set_operacao(ciclo, operacao='Falha ao abrir navegador')
            salvar_ciclo(pasta, ciclo)
            return ciclo

    # 1) Hub de cards (Bolões) — ainda SEM extrair
    try:
        if not ir_para_inicio_bolao(api.driver, log_fn=out_fn, espera=3.0):
            api.driver.get(getattr(api, 'URL_BOLOES', URL_INICIO_BOLAO))
            time.sleep(2)
    except Exception:
        pass

    # 2) Lê e mostra os cards ANTES do SIM
    cards = listar_cards_modalidades(api.driver, log_fn=out_fn)
    if wanted:
        cards = [c for c in cards if c['slug'] in wanted or c.get('parser_slug') in wanted]
    if not cards:
        out_fn('  [CICLO] Nenhum card reconhecido em inicio-bolao.')
        out_fn('  Abra Bolões no Edge (inicio-bolao) e rode [C] de novo.')
        ciclo['status'] = STATUS_CICLO_PAUSA
        set_operacao(ciclo, operacao='Sem cards')
        salvar_ciclo(pasta, ciclo)
        return ciclo

    ordem_slugs: List[str] = []
    for card in cards:
        slug = card['slug']
        label = card['label']
        ordem_slugs.append(slug)
        _garantir_unidade_lista(ciclo, slug, label)
        if card.get('concurso'):
            atualizar_modalidade(ciclo, slug, concurso=str(card['concurso']))
    if not continuar:
        extras = [s for s in list((ciclo.get('modalidades') or {}).keys()) if s not in ordem_slugs]
        for s in extras:
            ciclo['modalidades'].pop(s, None)

    salvar_ciclo(pasta, ciclo)
    imprimir_painel_ciclo(ciclo, out_fn, ordem_slugs=ordem_slugs)

    # 3) PAUSA — operador pode mexer no Caixa; nada baixa antes do SIM
    api.SESSAO_AUTORIZADA = False
    out_fn('\n  ⏸⏸⏸  PAUSA ATIVA — cards já detectados.')
    out_fn('  Mexa no Edge se precisar. Digite SIM no terminal para iniciar a extração.')
    if not api.aguardar_site_pronto(modo_ciclo=True):
        ciclo['status'] = STATUS_CICLO_PAUSA
        set_operacao(ciclo, operacao='Cancelado na confirmação inicial')
        salvar_ciclo(pasta, ciclo)
        return ciclo
    api.SESSAO_AUTORIZADA = True

    # Volta ao hub (usuário pode ter navegado no Edge durante a pausa)
    if not ir_para_inicio_bolao(api.driver, log_fn=out_fn, espera=2.0):
        ciclo['status'] = STATUS_CICLO_PAUSA
        set_operacao(ciclo, operacao='Falha ao reabrir inicio-bolao após SIM')
        salvar_ciclo(pasta, ciclo)
        return ciclo

    # Re-lê cards após SIM (site pode ter mudado enquanto esperava)
    cards_pos = listar_cards_modalidades(api.driver, log_fn=out_fn)
    if wanted and cards_pos:
        cards_pos = [c for c in cards_pos if c['slug'] in wanted or c.get('parser_slug') in wanted]
    if cards_pos:
        cards = cards_pos
        ordem_slugs = []
        for card in cards:
            slug = card['slug']
            label = card['label']
            ordem_slugs.append(slug)
            _garantir_unidade_lista(ciclo, slug, label)
            if card.get('concurso'):
                atualizar_modalidade(ciclo, slug, concurso=str(card['concurso']))
        salvar_ciclo(pasta, ciclo)

    out_fn('\n  ✔ SIM OK — iniciando extração automática (Compre seu bolão → 1 JSON/mod)...')
    imprimir_painel_ciclo(ciclo, out_fn, ordem_slugs=ordem_slugs)

    pausado = False
    total = len(cards)
    for idx, card in enumerate(cards, start=1):
        if pausado:
            break
        mod = _mod_de_card(card)
        if mod is None:
            out_fn(f'  [CICLO] Card sem modalidade: {card!r}')
            continue
        slug = mod.slug
        _garantir_unidade_lista(ciclo, slug, mod.label)
        lista = ciclo['modalidades'][slug]['ufs'][UNIDADE_LISTA]
        st_lista = str(lista.get('status') or '')
        if st_lista in (STATUS_CONCLUIDO, STATUS_VAZIO):
            out_fn(f'  [CICLO] {mod.label} já ok — pulando.')
            continue

        conc_mod = str(card.get('concurso') or ciclo['modalidades'][slug].get('concurso') or '')
        if conc_mod:
            atualizar_modalidade(ciclo, slug, concurso=conc_mod)

        atualizar_modalidade(ciclo, slug, status=STATUS_ANDAMENTO, marcar_inicio=True)
        set_operacao(
            ciclo,
            modalidade=slug,
            uf=UNIDADE_LISTA,
            operacao=f'Auto card — {mod.label} ({idx}/{total})',
        )
        atualizar_uf(ciclo, slug, UNIDADE_LISTA, status=STATUS_ANDAMENTO, incrementar_tentativa=True)
        salvar_ciclo(pasta, ciclo)
        imprimir_painel_ciclo(ciclo, out_fn, ordem_slugs=ordem_slugs)

        out_fn('')
        out_fn('=' * 64)
        out_fn(f'  {idx}/{total} — {mod.label} | conc {conc_mod or "?"} | AUTO')
        out_fn('=' * 64)

        ok_lista = abrir_lista_do_card(
            api.driver,
            label=mod.label,
            concurso=conc_mod,
            slug=slug,
            log_fn=out_fn,
        )
        if not ok_lista:
            atualizar_uf(
                ciclo, slug, UNIDADE_LISTA,
                status=STATUS_ERRO, erro='falha ao abrir Compre seu bolão',
            )
            atualizar_modalidade(ciclo, slug, status=STATUS_ERRO)
            salvar_ciclo(pasta, ciclo)
            out_fn(f'  [CICLO] Não abriu lista de {mod.label} — próxima.')
            continue

        # Retomada do CICLO (já decidida em [C/N] do ciclo) → força página.
        # Senão: None → perguntar_retomada só pergunta SE o JSON da mod existir.
        pagina_ini = None
        if st_lista in (STATUS_ANDAMENTO, STATUS_PAUSADO, STATUS_PENDENTE):
            pagina_ini = max(1, int(lista.get('pagina_atual') or 0) + 1)

        try:
            boloes_final, ab, painel = api._extrair_como_opcao_1(
                mod,
                conc_mod,
                rodada_filtro=idx,
                forcar_pagina_inicial=pagina_ini,
                sem_popup=True,
            )
        except Exception as exc:
            atualizar_uf(ciclo, slug, UNIDADE_LISTA, status=STATUS_ERRO, erro=str(exc))
            atualizar_modalidade(ciclo, slug, status=STATUS_ERRO)
            salvar_ciclo(pasta, ciclo)
            out_fn(f'  [CICLO] Erro em {mod.label}: {exc} — próxima.')
            continue

        if ab:
            atualizar_modalidade(ciclo, slug, arquivo_base=ab)
        conc = (painel or {}).get('concurso_alvo') or conc_mod
        if conc:
            atualizar_modalidade(ciclo, slug, concurso=conc)

        paginas = int((painel or {}).get('paginas_processadas') or 0)
        total_pag = int(((painel or {}).get('paginacao_api') or {}).get('ultima_pagina') or 0)
        n_boloes = len(boloes_final or [])
        path_json = os.path.join(pasta, f'{(ab or "x")}.json')
        try:
            from boloes_consolidar import carregar_json_boloes
            disco = carregar_json_boloes(path_json)
            n_boloes = max(n_boloes, len(disco))
            ufs_found = _ufs_dos_boloes(disco or boloes_final)
        except Exception:
            ufs_found = _ufs_dos_boloes(boloes_final)
        if ufs_found:
            ciclo['modalidades'][slug]['ufs_encontradas'] = ', '.join(ufs_found)

        if bool((painel or {}).get('pausado_operador')):
            atualizar_uf(
                ciclo, slug, UNIDADE_LISTA,
                status=STATUS_PAUSADO, pagina_atual=paginas,
                total_paginas=total_pag, boloes=n_boloes,
            )
            atualizar_modalidade(ciclo, slug, status=STATUS_PAUSADO)
            ciclo['status'] = STATUS_CICLO_PAUSA
            set_operacao(ciclo, operacao='Pausado pelo operador')
            salvar_ciclo(pasta, ciclo)
            pausado = True
            break

        if (painel or {}).get('uf_concluida') or (painel or {}).get('chegou_ao_fim'):
            st = STATUS_VAZIO if (paginas <= 0 and n_boloes <= 0) else STATUS_CONCLUIDO
            atualizar_uf(
                ciclo, slug, UNIDADE_LISTA,
                status=st, pagina_atual=paginas,
                total_paginas=total_pag, boloes=n_boloes,
            )
            atualizar_modalidade(ciclo, slug, status=st, marcar_fim=True)
            out_fn(f'\n  [OK] {mod.label} concluída → JSON isolado.')
            try:
                api._aviso_sonoro_extracao_completa()
            except Exception:
                pass
        elif paginas <= 0 and n_boloes <= 0:
            atualizar_uf(
                ciclo, slug, UNIDADE_LISTA,
                status=STATUS_PENDENTE, pagina_atual=0, boloes=0,
                erro='lista vazia',
            )
            atualizar_modalidade(ciclo, slug, status=STATUS_PENDENTE)
        else:
            atualizar_uf(
                ciclo, slug, UNIDADE_LISTA,
                status=STATUS_PENDENTE, pagina_atual=paginas,
                total_paginas=total_pag, boloes=n_boloes,
                erro='parcial',
            )
            atualizar_modalidade(ciclo, slug, status=STATUS_PENDENTE)

        salvar_ciclo(pasta, ciclo)
        imprimir_painel_ciclo(ciclo, out_fn, ordem_slugs=ordem_slugs)

        # Volta ao hub antes da próxima modalidade
        try:
            ir_para_inicio_bolao(api.driver, log_fn=out_fn, espera=2.0)
        except Exception:
            pass

    if not pausado:
        todas_ok = True
        for slug in ordem_slugs:
            st = str(ciclo['modalidades'].get(slug, {}).get('status') or '')
            if st != STATUS_CONCLUIDO:
                if modalidade_concluida_pelas_ufs(ciclo, slug):
                    atualizar_modalidade(ciclo, slug, status=STATUS_CONCLUIDO, marcar_fim=True)
                else:
                    todas_ok = False
        from boloes_checkpoint_ciclo import agora_iso
        ciclo['fim'] = agora_iso()
        ciclo['status'] = STATUS_CICLO_OK if todas_ok else STATUS_CICLO_PAUSA
        set_operacao(ciclo, modalidade='', uf='', operacao='Finalizado')
        salvar_ciclo(pasta, ciclo)
        imprimir_resumo_final(ciclo, out_fn)
        try:
            api._aviso_sonoro_extracao_completa()
        except Exception:
            pass
    else:
        salvar_ciclo(pasta, ciclo)
        out_fn('\n  [CICLO] Pausado. Rode [C] e Continuar.')

    return ciclo
