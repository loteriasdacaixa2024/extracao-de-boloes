# -*- coding: utf-8 -*-
"""
Checkpoint / retomada da extração de bolões (API).

Não altera login, navegação nem parsing — só persiste progresso entre páginas
para permitir pausar e continuar do próximo número de página.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

STATUS_EXECUTANDO = 'Em execução'
STATUS_PAUSADO = 'Pausado'
STATUS_CONCLUIDO = 'Concluído'

_NOME_CHECKPOINT = '_checkpoint_extracao.json'
_NOME_PAUSE = '_PAUSE.request'

# Flag em memória (thread do terminal pode setar)
_pause_solicitada = False
_pause_lock = threading.Lock()


def caminho_checkpoint(pasta_json: str) -> str:
    return os.path.join(pasta_json, _NOME_CHECKPOINT)


def caminho_pause_request(pasta_json: str) -> str:
    return os.path.join(pasta_json, _NOME_PAUSE)


def agora_iso() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def limpar_pause_request(pasta_json: str) -> None:
    path = caminho_pause_request(pasta_json)
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def solicitar_pause(pasta_json: str) -> None:
    """Cria arquivo de pedido de pausa (termina a página atual e para)."""
    global _pause_solicitada
    with _pause_lock:
        _pause_solicitada = True
    os.makedirs(pasta_json, exist_ok=True)
    with open(caminho_pause_request(pasta_json), 'w', encoding='utf-8') as f:
        f.write(agora_iso())


def reset_pause_flags(pasta_json: str) -> None:
    global _pause_solicitada
    with _pause_lock:
        _pause_solicitada = False
    limpar_pause_request(pasta_json)


def pause_solicitada(pasta_json: str) -> bool:
    with _pause_lock:
        if _pause_solicitada:
            return True
    if os.path.isfile(caminho_pause_request(pasta_json)):
        return True
    # Tecla P sem Enter (Windows)
    try:
        import msvcrt
        while msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ('p', 'P'):
                solicitar_pause(pasta_json)
                return True
    except Exception:
        pass
    return False


def instruir_pause(pasta_json: str, out_fn: Callable[[str], None] = print) -> None:
    out_fn('  [CHECKPOINT] PAUSAR: pressione a tecla P (sem Enter) ou crie:')
    out_fn(f'               {caminho_pause_request(pasta_json)}')


def carregar_checkpoint(pasta_json: str) -> Optional[Dict[str, Any]]:
    path = caminho_checkpoint(pasta_json)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def salvar_checkpoint(
    pasta_json: str,
    *,
    modalidade: str = '',
    modalidade_label: str = '',
    concurso: str = '',
    arquivo_base: str = '',
    pagina_atual: int = 0,
    total_paginas: int = 0,
    boloes_extraidos: int = 0,
    status: str = STATUS_EXECUTANDO,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """Grava checkpoint atômico (tmp + replace)."""
    os.makedirs(pasta_json, exist_ok=True)
    path = caminho_checkpoint(pasta_json)
    payload: Dict[str, Any] = {
        'modalidade': modalidade or '',
        'modalidade_label': modalidade_label or '',
        'concurso': re.sub(r'\D', '', str(concurso or '')) or '',
        'arquivo_base': (arquivo_base or '').removesuffix('.json'),
        'pagina_atual': int(pagina_atual or 0),
        'proxima_pagina': int(pagina_atual or 0) + 1 if status != STATUS_CONCLUIDO else 0,
        'total_paginas': int(total_paginas or 0),
        'boloes_extraidos': int(boloes_extraidos or 0),
        'atualizado_em': agora_iso(),
        'status': status,
    }
    if extra:
        payload.update(extra)

    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def checkpoint_incompleto(ck: Optional[Dict[str, Any]]) -> bool:
    if not ck:
        return False
    status = str(ck.get('status') or '')
    if status == STATUS_CONCLUIDO:
        return False
    return int(ck.get('pagina_atual') or 0) > 0


def ultima_pagina_do_json(boloes: List[dict]) -> int:
    """Maior campo 'pagina' já gravado com sucesso no JSON de sessão."""
    mx = 0
    for b in boloes or []:
        try:
            p = int(b.get('pagina') or 0)
        except (TypeError, ValueError):
            p = 0
        if p > mx:
            mx = p
    return mx


def resolver_pagina_retomada(
    pasta_json: str,
    arquivo_base: str,
    boloes_existentes: List[dict],
) -> Tuple[int, Optional[Dict[str, Any]]]:
    """
    Retorna (ultima_pagina_ok, checkpoint).
    Prefere checkpoint; se ausente, usa max(pagina) do JSON.
    """
    ck = carregar_checkpoint(pasta_json)
    pagina_json = ultima_pagina_do_json(boloes_existentes)
    pagina_ck = int((ck or {}).get('pagina_atual') or 0)

    # Se checkpoint aponta para outro arquivo, ignora (exceto se vazio)
    if ck and arquivo_base:
        ab_ck = str(ck.get('arquivo_base') or '').removesuffix('.json')
        ab = arquivo_base.removesuffix('.json')
        if ab_ck and ab and ab_ck != ab and pagina_json > 0:
            # prioriza o JSON da sessão atual
            return pagina_json, None

    ultima = max(pagina_ck, pagina_json)
    if ultima <= 0:
        return 0, ck if checkpoint_incompleto(ck) else None
    return ultima, ck


def perguntar_retomada(
    pasta_json: str,
    arquivo_base: str,
    boloes_existentes: List[dict],
    *,
    input_fn: Callable[[str], str] = input,
    out_fn: Callable[[str], None] = print,
) -> Tuple[int, bool]:
    """
    Se houver extração incompleta, pergunta ao usuário.
    Retorna (pagina_inicial, retomou).
    pagina_inicial = 1 (nova) ou ultima+1 (continuar).
    """
    ultima, ck = resolver_pagina_retomada(pasta_json, arquivo_base, boloes_existentes)
    if ultima <= 0:
        return 1, False

    status = (ck or {}).get('status') or ''
    total = int((ck or {}).get('total_paginas') or 0)
    # Já concluído até o fim → não oferece
    if status == STATUS_CONCLUIDO and (not total or ultima >= total):
        return 1, False
    if status == STATUS_CONCLUIDO and not checkpoint_incompleto(ck) and ultima >= total > 0:
        return 1, False

    # Sem checkpoint mas JSON parcial: ainda oferece retomada
    if not ck and ultima > 0:
        status = STATUS_PAUSADO
    qtd = int((ck or {}).get('boloes_extraidos') or len(boloes_existentes) or 0)
    mod = (ck or {}).get('modalidade_label') or (ck or {}).get('modalidade') or '?'
    conc = (ck or {}).get('concurso') or '?'
    quando = (ck or {}).get('atualizado_em') or '?'

    out_fn('')
    out_fn('=' * 60)
    out_fn('  EXTRACAO INCOMPLETA DETECTADA')
    out_fn('=' * 60)
    out_fn(f'  Modalidade : {mod}')
    out_fn(f'  Concurso   : {conc}')
    out_fn(f'  Ultima pag.: {ultima}' + (f' / {total}' if total else ''))
    out_fn(f'  Boloes     : {qtd}')
    out_fn(f'  Status     : {status}')
    out_fn(f'  Atualizado : {quando}')
    ab_show = str((ck or {}).get('arquivo_base') or arquivo_base or '').replace('_CONSOLIDADO', '')
    out_fn(f'  Arquivo    : {ab_show}.json')
    out_fn('-' * 60)
    out_fn(f'  [C] Continuar da pagina {ultima + 1}')
    out_fn('  [N] Nova extracao (desde a pagina 1)')
    out_fn('=' * 60)

    try:
        resp = (input_fn('  Escolha [C/N] (Enter=Continuar): ') or '').strip().upper()
    except EOFError:
        resp = 'C'

    if resp in ('N', 'NOVA', 'NEW'):
        out_fn('  >> Nova extracao a partir da pagina 1.')
        return 1, False

    out_fn(f'  >> Continuando a partir da pagina {ultima + 1}.')
    return ultima + 1, True
