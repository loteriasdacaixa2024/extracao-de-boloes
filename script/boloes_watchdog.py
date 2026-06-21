# -*- coding: utf-8 -*-
"""
Watchdog — Monitora a pasta json-boloes/ e consolida automaticamente.

Sempre que um arquivo .json novo for inserido (ou modificado) na pasta,
ele é lido, deduplicado por hash_bolao e mesclado no arquivo CONSOLIDADO
correspondente (por concurso + modalidade).

Uso:
  python boloes_watchdog.py            # modo contínuo (monitora em tempo real)
  python boloes_watchdog.py --once     # consolida uma única vez e sai
  python boloes_watchdog.py --status   # mostra o estado atual dos consolidados
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import hashlib
from typing import Any, Dict, List, Optional, Set, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from boloes_modalidades import (
    extrair_concurso_de_boloes,
    extrair_modalidade_de_boloes,
    nome_arquivo_sessao,
)
from boloes_consolidar import (
    carregar_json_boloes,
    mesclar_listas,
    nome_arquivo_consolidado,
    salvar_json_boloes,
)

# --- Configuração ---
CONFERENCIAS_BOLOES_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, '..'))
PASTA_JSON = os.path.join(CONFERENCIAS_BOLOES_DIR, 'json-boloes')

# Arquivos a ignorar (consolidados não se consolidam a si mesmos)
IGNORAR_SUFFIXOS = ('_CONSOLIDADO.json',)
IGNORAR_PREFIXOS = ('_',)  # arquivos de sistema internos

# Intervalo de polling em segundos (modo contínuo)
POLL_INTERVAL = 3

# Debounce: ignora arquivos modificados há menos que N segundos
# (evita ler arquivo ainda sendo escrito)
DEBOUNCE_SEGUNDOS = 2


def _log(msg: str) -> None:
    ts = time.strftime('%H:%M:%S')
    # Compatibilidade Windows (console sem UTF-8 para emojis)
    try:
        print(f'[{ts}] {msg}', flush=True)
    except UnicodeEncodeError:
        print(f'[{ts}] {msg.encode("ascii", errors="replace").decode("ascii")}', flush=True)


def _arquivo_ignorado(nome: str) -> bool:
    """Retorna True se o arquivo não deve ser processado como fonte."""
    if nome.endswith(IGNORAR_SUFFIXOS):
        return True
    if any(nome.startswith(p) for p in IGNORAR_PREFIXOS):
        return True
    return False


def _hash_de(bolao: dict) -> Optional[str]:
    return bolao.get('hash_bolao') or None


def _kb(path: str) -> float:
    try:
        return os.path.getsize(path) / 1024
    except OSError:
        return 0.0


def _stable_hash_conteudo(path: str) -> str:
    """Hash MD5 do conteúdo do arquivo para detectar mudanças reais."""
    try:
        with open(path, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()
    except OSError:
        return ''


def listar_arquivos_fonte() -> List[str]:
    """Lista todos os arquivos .json na pasta que são fonte (não consolidados)."""
    if not os.path.isdir(PASTA_JSON):
        return []
    arquivos = []
    for f in sorted(glob.glob(os.path.join(PASTA_JSON, '*.json'))):
        nome = os.path.basename(f)
        if not _arquivo_ignorado(nome):
            arquivos.append(f)
    return arquivos


def extrair_boloes_de_arquivo(path: str) -> List[dict]:
    """Carrega bolões de um arquivo JSON (lista direta ou com chave 'boloes')."""
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        _log(f'  [ERRO] Falha ao ler {os.path.basename(path)}: {e}')
        return []

    if isinstance(data, list):
        return data

    # Tenta chave alternativa
    if isinstance(data, dict):
        for chave in ('boloes', 'apostas', 'jogos', 'dados', 'items', 'results'):
            if chave in data and isinstance(data[chave], list):
                return data[chave]

    _log(f'  [AVISO] {os.path.basename(path)}: formato não reconhecido (esperava lista)')
    return []


def _agrupar_por_concurso_modalidade(
    boloes: List[dict],
) -> Dict[Tuple[str, str], List[dict]]:
    """Agrupa bolões por (concurso, modalidade_slug)."""
    grupos: Dict[Tuple[str, str], List[dict]] = {}

    # Primeiro, tenta extrair concurso e modalidade do conjunto inteiro
    concurso = extrair_concurso_de_boloes(boloes)
    mod = extrair_modalidade_de_boloes(boloes)
    mod_slug = mod.slug if mod else 'desconhecido'

    # Agrupa cada bolão individualmente (um arquivo pode ter várias modalidades)
    for b in boloes:
        b_concurso = str(b.get('concurso') or concurso or 'sem-concurso')
        b_mod_slug = str(b.get('modalidade_slug') or b.get('modalidade') or mod_slug)

        # Normaliza o slug
        b_mod_slug = b_mod_slug.lower().strip().replace(' ', '-')
        if not b_mod_slug or b_mod_slug == 'none':
            b_mod_slug = mod_slug

        chave = (b_concurso, b_mod_slug)
        if chave not in grupos:
            grupos[chave] = []
        grupos[chave].append(b)

    return grupos


def consolidar_tudo() -> Dict[str, Any]:
    """
    Lê todos os arquivos fonte em json-boloes/, agrupa por concurso+modalidade
    e consolida cada grupo no seu _CONSOLIDADO.json correspondente.

    Retorna um resumo do que foi feito.
    """
    arquivos = listar_arquivos_fonte()
    if not arquivos:
        _log('Nenhum arquivo fonte encontrado em json-boloes/.')
        return {'processados': 0, 'consolidados': {}}

    _log(f'{len(arquivos)} arquivo(s) fonte encontrado(s).')

    # Coleta TODOS os bolões de TODOS os arquivos
    todos_boloes: List[dict] = []
    erros = 0
    for path in arquivos:
        nome = os.path.basename(path)
        boloes = extrair_boloes_de_arquivo(path)
        if boloes:
            _log(f'  {nome}: {len(boloes)} bolão(ões)')
            todos_boloes.extend(boloes)
        else:
            # Pode ser arquivo vazio ou em formato desconhecido
            try:
                with open(path, encoding='utf-8') as f:
                    raw = f.read().strip()
                if raw and raw != '[]':
                    erros += 1
                    _log(f'  {nome}: 0 bolões extraídos (formato não reconhecido)')
            except Exception:
                erros += 1

    if not todos_boloes:
        _log('Nenhum bolão extraído dos arquivos.')
        return {'processados': len(arquivos), 'consolidados': {}, 'erros': erros}

    _log(f'Total extraído: {len(todos_boloes)} bolões de {len(arquivos)} arquivo(s).')

    # Agrupa por concurso + modalidade
    grupos = _agrupar_por_concurso_modalidade(todos_boloes)

    resultados = {}
    for (concurso, mod_slug), boloes_grupo in sorted(grupos.items()):
        nome_consolidado = nome_arquivo_consolidado(concurso, mod_slug)
        path_consolidado = os.path.join(PASTA_JSON, nome_consolidado)

        # Carrega consolidado existente
        anteriores = carregar_json_boloes(path_consolidado)

        # Mescla
        final, novos = mesclar_listas(anteriores, boloes_grupo)

        # Salva
        salvar_json_boloes(path_consolidado, final)

        kb = _kb(path_consolidado)
        _log(
            f'  [OK] {nome_consolidado}: '
            f'{len(anteriores)} -> {len(final)} '
            f'(+{novos} novos, {kb:.1f} KB)'
        )

        resultados[nome_consolidado] = {
            'anteriores': len(anteriores),
            'novos': novos,
            'total': len(final),
            'kb': round(kb, 1),
        }

    return {
        'processados': len(arquivos),
        'total_boloes': len(todos_boloes),
        'consolidados': resultados,
        'erros': erros,
    }


def mostrar_status() -> None:
    """Mostra o estado atual dos arquivos consolidados."""
    print(f'\n{"=" * 60}')
    print(f'  STATUS — Pasta: {PASTA_JSON}')
    print(f'{"=" * 60}\n')

    # Arquivos fonte
    fontes = listar_arquivos_fonte()
    print(f'  Arquivos fonte: {len(fontes)}')
    for f in fontes:
        nome = os.path.basename(f)
        kb = _kb(f)
        qtd = len(extrair_boloes_de_arquivo(f))
        print(f'    - {nome}  ({qtd} boloes, {kb:.1f} KB)')

    # Consolidados
    consolidados = sorted(glob.glob(os.path.join(PASTA_JSON, '*_CONSOLIDADO.json')))
    print(f'\n  Consolidados: {len(consolidados)}')
    total_geral = 0
    for c in consolidados:
        nome = os.path.basename(c)
        kb = _kb(c)
        boloes = carregar_json_boloes(c)
        print(f'    [OK] {nome}  ({len(boloes)} boloes, {kb:.1f} KB)')
        total_geral += len(boloes)

    print(f'\n  Total geral nos consolidados: {total_geral} bolões')
    print()


def monitorar_continuo() -> None:
    """
    Monitora a pasta json-boloes/ em tempo real.
    Ao detectar novo arquivo ou modificação, consolida automaticamente.
    """
    os.makedirs(PASTA_JSON, exist_ok=True)

    _log(f'Watchdog iniciado — monitorando: {PASTA_JSON}')
    _log(f'Pressione CTRL+C para parar.\n')

    # Estado: caminho -> hash do conteúdo (para detectar mudanças)
    estado: Dict[str, str] = {}

    # Inicializa o estado sem disparar consolidação
    for f in listar_arquivos_fonte():
        estado[f] = _stable_hash_conteudo(f)

    _log(f'{len(estado)} arquivo(s) já existente(s) — ignorando na primeira passagem.')

    try:
        while True:
            time.sleep(POLL_INTERVAL)

            arquivos_atuais = set(listar_arquivos_fonte())
            arquivos_conhecidos = set(estado.keys())

            # Arquivos novos
            novos = arquivos_atuais - arquivos_conhecidos
            # Arquivos removidos
            removidos = arquivos_conhecidos - arquivos_atuais
            # Arquíveis que podem ter mudado
            comuns = arquivos_atuais & arquivos_conhecidos

            mudou = False

            # Registra novos
            for f in novos:
                # Debounce: espera o arquivo estabilizar
                mtime = os.path.getmtime(f)
                if time.time() - mtime < DEBOUNCE_SEGUNDOS:
                    continue
                estado[f] = _stable_hash_conteudo(f)
                _log(f'  [NOVO] {os.path.basename(f)}')
                mudou = True

            # Remove os que sumiram
            for f in removidos:
                del estado[f]
                _log(f'  [REMOVIDO] {os.path.basename(f)}')
                mudou = True

            # Verifica modificações
            for f in comuns:
                mtime = os.path.getmtime(f)
                if time.time() - mtime < DEBOUNCE_SEGUNDOS:
                    continue
                h = _stable_hash_conteudo(f)
                if h != estado[f]:
                    estado[f] = h
                    _log(f'  [MODIFICADO] {os.path.basename(f)}')
                    mudou = True

            if mudou:
                _log('  → Consolidando...')
                try:
                    resumo = consolidar_tudo()
                    if resumo.get('consolidados'):
                        for nome, info in resumo['consolidados'].items():
                            _log(f'    ✔ {nome}: {info["total"]} bolões ({info["kb"]} KB)')
                except Exception as e:
                    _log(f'  [ERRO] Falha na consolidação: {e}')

    except KeyboardInterrupt:
        _log('\nWatchdog encerrado (CTRL+C).')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Watchdog — Consolida bolões automaticamente em json-boloes/',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        '--once', action='store_true',
        help='Consolida uma única vez e sai',
    )
    parser.add_argument(
        '--status', action='store_true',
        help='Mostra o estado atual dos consolidados',
    )
    args = parser.parse_args()

    os.makedirs(PASTA_JSON, exist_ok=True)

    if args.status:
        mostrar_status()
    elif args.once:
        _log('Consolidação única...')
        resumo = consolidar_tudo()
        if resumo.get('consolidados'):
            _log('\nResumo:')
            for nome, info in resumo['consolidados'].items():
                _log(f'  {nome}: {info["anteriores"]} → {info["total"]} (+{info["novos"]})')
        else:
            _log('Nada para consolidar.')
    else:
        monitorar_continuo()


if __name__ == '__main__':
    main()
