# -*- coding: utf-8 -*-
"""
Checkpoint GLOBAL do ciclo completo (modalidade × UF × página).

Arquivo: json-boloes/_checkpoint_ciclo.json
Não substitui _checkpoint_extracao.json (página local) — complementa.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

NOME_CHECKPOINT_CICLO = '_checkpoint_ciclo.json'

STATUS_AGUARDANDO = 'AGUARDANDO'
STATUS_ANDAMENTO = 'EM ANDAMENTO'
STATUS_CONCLUIDO = 'CONCLUÍDO'
STATUS_ERRO = 'ERRO'
STATUS_PENDENTE = 'PENDENTE'
STATUS_VAZIO = 'VAZIO'
STATUS_PAUSADO = 'PAUSADO'
STATUS_CICLO_EXEC = 'EM EXECUÇÃO'
STATUS_CICLO_OK = 'EXTRAÇÃO COMPLETA'
STATUS_CICLO_PAUSA = 'PAUSADO'


def agora_iso() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def caminho_checkpoint_ciclo(pasta_json: str) -> str:
    return os.path.join(pasta_json, NOME_CHECKPOINT_CICLO)


def _atomic_write(path: str, payload: Dict[str, Any]) -> str:
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def carregar_ciclo(pasta_json: str) -> Optional[Dict[str, Any]]:
    path = caminho_checkpoint_ciclo(pasta_json)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def novo_ciclo(
    modalidades: List[Dict[str, str]],
    ufs: List[str],
    *,
    ciclo_id: str = '',
) -> Dict[str, Any]:
    """
    modalidades: [{'slug','label'}, ...]
    ufs: ['SP','AC', ...]
    """
    cid = ciclo_id or datetime.now().strftime('%Y%m%d-%H%M%S')
    mods: Dict[str, Any] = {}
    for m in modalidades:
        slug = m['slug']
        ufs_map = {
            sigla: {
                'status': STATUS_AGUARDANDO,
                'pagina_atual': 0,
                'total_paginas': 0,
                'boloes': 0,
                'tentativas': 0,
                'erro': '',
                'atualizado_em': '',
            }
            for sigla in ufs
        }
        mods[slug] = {
            'label': m.get('label') or slug,
            'status': STATUS_AGUARDANDO,
            'concurso': '',
            'arquivo_base': '',
            'ufs': ufs_map,
            'inicio': '',
            'fim': '',
        }
    return {
        'versao': 2,
        'ciclo_id': cid,
        'status': STATUS_CICLO_EXEC,
        'inicio': agora_iso(),
        'fim': '',
        'atualizado_em': agora_iso(),
        'modalidade_atual': '',
        'uf_atual': '',
        'operacao': 'Iniciando',
        'modalidades': mods,
        'resumo': {
            'modalidades_ok': 0,
            'ufs_ok': 0,
            'ufs_erro': 0,
            'ufs_vazio': 0,
            'ufs_pendente': 0,
            'boloes_total': 0,
            'erros': 0,
        },
    }


def salvar_ciclo(pasta_json: str, ciclo: Dict[str, Any]) -> str:
    ciclo = dict(ciclo)
    ciclo['atualizado_em'] = agora_iso()
    ciclo['resumo'] = calcular_resumo(ciclo)
    return _atomic_write(caminho_checkpoint_ciclo(pasta_json), ciclo)


def calcular_resumo(ciclo: Dict[str, Any]) -> Dict[str, int]:
    mods_ok = 0
    ufs_ok = ufs_erro = ufs_vazio = ufs_pend = boloes = erros = 0
    for mod in (ciclo.get('modalidades') or {}).values():
        if str(mod.get('status') or '') == STATUS_CONCLUIDO:
            mods_ok += 1
        for uf in (mod.get('ufs') or {}).values():
            st = str(uf.get('status') or '')
            boloes += int(uf.get('boloes') or 0)
            if st == STATUS_CONCLUIDO:
                ufs_ok += 1
            elif st == STATUS_ERRO:
                ufs_erro += 1
                erros += 1
            elif st == STATUS_VAZIO:
                ufs_vazio += 1
            elif st in (STATUS_PENDENTE, STATUS_AGUARDANDO, STATUS_ANDAMENTO, STATUS_PAUSADO):
                if st == STATUS_PENDENTE:
                    ufs_pend += 1
    return {
        'modalidades_ok': mods_ok,
        'ufs_ok': ufs_ok,
        'ufs_erro': ufs_erro,
        'ufs_vazio': ufs_vazio,
        'ufs_pendente': ufs_pend,
        'boloes_total': boloes,
        'erros': erros,
    }


def set_operacao(
    ciclo: Dict[str, Any],
    *,
    modalidade: str = '',
    uf: str = '',
    operacao: str = '',
) -> None:
    if modalidade:
        ciclo['modalidade_atual'] = modalidade
    if uf:
        ciclo['uf_atual'] = uf
    if operacao:
        ciclo['operacao'] = operacao


def atualizar_uf(
    ciclo: Dict[str, Any],
    slug: str,
    sigla: str,
    *,
    status: str = '',
    pagina_atual: Optional[int] = None,
    total_paginas: Optional[int] = None,
    boloes: Optional[int] = None,
    erro: Optional[str] = None,
    incrementar_tentativa: bool = False,
) -> None:
    mod = (ciclo.get('modalidades') or {}).get(slug)
    if not mod:
        return
    uf = (mod.get('ufs') or {}).get(sigla)
    if not uf:
        return
    if status:
        uf['status'] = status
    if pagina_atual is not None:
        uf['pagina_atual'] = int(pagina_atual)
    if total_paginas is not None:
        uf['total_paginas'] = int(total_paginas)
    if boloes is not None:
        uf['boloes'] = int(boloes)
    if erro is not None:
        uf['erro'] = str(erro)[:500]
    if incrementar_tentativa:
        uf['tentativas'] = int(uf.get('tentativas') or 0) + 1
    uf['atualizado_em'] = agora_iso()


def atualizar_modalidade(
    ciclo: Dict[str, Any],
    slug: str,
    *,
    status: str = '',
    concurso: str = '',
    arquivo_base: str = '',
    marcar_inicio: bool = False,
    marcar_fim: bool = False,
) -> None:
    mod = (ciclo.get('modalidades') or {}).get(slug)
    if not mod:
        return
    if status:
        mod['status'] = status
    if concurso:
        mod['concurso'] = re.sub(r'\D', '', str(concurso)) or mod.get('concurso') or ''
    if arquivo_base:
        mod['arquivo_base'] = str(arquivo_base).removesuffix('.json')
    if marcar_inicio and not mod.get('inicio'):
        mod['inicio'] = agora_iso()
    if marcar_fim:
        mod['fim'] = agora_iso()


def modalidade_concluida_pelas_ufs(ciclo: Dict[str, Any], slug: str) -> bool:
    mod = (ciclo.get('modalidades') or {}).get(slug) or {}
    ufs = mod.get('ufs') or {}
    if not ufs:
        return False
    terminais = {STATUS_CONCLUIDO, STATUS_VAZIO, STATUS_ERRO}
    return all(str(u.get('status') or '') in terminais for u in ufs.values())


def proximo_pendente(ciclo: Dict[str, Any]) -> Optional[tuple]:
    """
    Retorna (slug, sigla, pagina_retomar) do próximo item a processar.
    pagina_retomar = pagina_atual+1 se EM ANDAMENTO/PAUSADO com página > 0.
    """
    for slug, mod in (ciclo.get('modalidades') or {}).items():
        st_mod = str(mod.get('status') or '')
        if st_mod == STATUS_CONCLUIDO:
            continue
        for sigla, uf in (mod.get('ufs') or {}).items():
            st = str(uf.get('status') or '')
            if st in (STATUS_CONCLUIDO, STATUS_VAZIO, STATUS_ERRO):
                continue
            pagina = int(uf.get('pagina_atual') or 0)
            if st in (STATUS_ANDAMENTO, STATUS_PAUSADO) and pagina > 0:
                return slug, sigla, pagina + 1
            return slug, sigla, 1
    return None


def ufs_concluidas_da_modalidade(ciclo: Dict[str, Any], slug: str) -> List[str]:
    mod = (ciclo.get('modalidades') or {}).get(slug) or {}
    out = []
    for sigla, uf in (mod.get('ufs') or {}).items():
        if str(uf.get('status') or '') in (STATUS_CONCLUIDO, STATUS_VAZIO):
            out.append(sigla)
    return out


def perguntar_retomada_ciclo(
    pasta_json: str,
    *,
    input_fn=input,
    out_fn=print,
) -> tuple:
    """
    Retorna (continuar: bool, ciclo: dict|None).
    continuar=False + ciclo=None → usuário quer ciclo NOVO (descarta progresso do checkpoint).
    """
    ciclo = carregar_ciclo(pasta_json)
    if not ciclo:
        return True, None

    status = str(ciclo.get('status') or '')
    if status == STATUS_CICLO_OK:
        out_fn('\n  [CICLO] Último ciclo já está COMPLETO.')
        out_fn('  [C] Continuar mesmo assim (revisar pendências/erros)')
        out_fn('  [N] Novo ciclo do zero')
    else:
        nxt = proximo_pendente(ciclo)
        out_fn('')
        out_fn('=' * 60)
        out_fn('  CICLO INCOMPLETO DETECTADO')
        out_fn('=' * 60)
        out_fn(f'  ID        : {ciclo.get("ciclo_id")}')
        out_fn(f'  Status    : {status}')
        out_fn(f'  Início    : {ciclo.get("inicio")}')
        out_fn(f'  Atualizado: {ciclo.get("atualizado_em")}')
        out_fn(f'  Modalidade: {ciclo.get("modalidade_atual") or "?"}')
        out_fn(f'  UF        : {ciclo.get("uf_atual") or "?"}')
        if nxt:
            out_fn(f'  Próximo   : {nxt[0]} / {nxt[1]} pág. {nxt[2]}')
        res = ciclo.get('resumo') or calcular_resumo(ciclo)
        out_fn(
            f'  Resumo    : mods_ok={res.get("modalidades_ok")} | '
            f'ufs_ok={res.get("ufs_ok")} | erros={res.get("erros")} | '
            f'bolões≈{res.get("boloes_total")}'
        )
        out_fn('-' * 60)
        out_fn('  [C] Continuar de onde parou')
        out_fn('  [N] Novo ciclo (NÃO apaga JSONs; reinicia só o checkpoint do ciclo)')
        out_fn('=' * 60)

    try:
        resp = (input_fn('  Escolha [C/N] (Enter=Continuar): ') or '').strip().upper()
    except EOFError:
        resp = 'C'

    if resp in ('N', 'NOVA', 'NEW'):
        out_fn('  >> Novo ciclo a partir da primeira modalidade/UF.')
        return False, None

    out_fn('  >> Continuando o ciclo existente.')
    return True, ciclo
