# -*- coding: utf-8 -*-
"""
Extração de bolões via API silce-servico-rest (Caixa).

A URL usa parâmetro ?q= criptografado (gerado pelo JS do site) — não dá para
montar manualmente. Por isso interceptamos fetch/XHR no navegador logado e
parseamos o JSON das respostas.

Endpoint exemplo (path Base64):
  Ym9sb2VzL2RldGFsaGFyLWJvbGFv  ->  boloes/detalhar-bolao
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional, Set

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from boloes_api_parser import extrair_todos_boloes_json, parse_lista_boloes_api

API_PREFIX = 'silce-servico-rest'

LEGENDA_API = """
  Modo [1] VISIVEL: clica Detalhes e voce ve o popup abrir/fechar.
  Modo [C] RAPIDO: MESMO fluxo do [1], mas baixa via API (detalhar-bolao) — SEM popup.
  Contagem: botoes Detalhes = meta por pagina. JSON via interceptacao API.
"""


def _explicar_via_js(via: str, log_fn: Optional[Callable[[str], None]]) -> None:
    if not log_fn:
        return
    v = (via or '').lower()
    if 'bolaocaixa' in v or 'detalharbolao' in v or 'detalhar' in v:
        log_fn('  → Melhor caso: sem popup visivel, bem mais rapido (~0,2 s/bolao).')
    elif 'detalhes-click' in v or 'silent-click' in v:
        log_fn('  → Clique nos botoes Detalhes visiveis (modal oculto).')
    elif v and v != '?':
        log_fn(f'  → Via {via} — aguardando JSON das capturas.')

_HOOK_JS = """
(function () {
  if (window.__boloesApiHook) return;
  window.__boloesApiHook = true;
  window.__boloesApiCapturas = [];

  function registrar(url, data) {
    try {
      if (!url || String(url).indexOf('silce-servico-rest') < 0) return;
      window.__boloesApiCapturas.push({
        url: String(url),
        ts: Date.now(),
        data: data
      });
    } catch (e) {}
  }

  if (window.fetch) {
    var origFetch = window.fetch;
    window.fetch = function () {
      var args = arguments;
      return origFetch.apply(this, args).then(function (res) {
        var url = (args[0] && (args[0].url || args[0])) || '';
        try {
          var clone = res.clone();
          clone.json().then(function (data) { registrar(url, data); }).catch(function () {});
        } catch (e) {}
        return res;
      });
    };
  }

  var xOpen = XMLHttpRequest.prototype.open;
  var xSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__boloesApiUrl = url;
    return xOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function () {
    var xhr = this;
    xhr.addEventListener('load', function () {
      try {
        var data = xhr.responseType === 'json' ? xhr.response : JSON.parse(xhr.responseText);
        registrar(xhr.__boloesApiUrl, data);
      } catch (e) {}
    });
    return xSend.apply(this, arguments);
  };
})();
"""


def instalar_interceptador_api(driver) -> None:
    """Instala hook antes de navegar (captura fetch/XHR da API Caixa)."""
    try:
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {'source': _HOOK_JS})
    except Exception:
        pass
    try:
        driver.execute_script(_HOOK_JS)
    except Exception:
        pass


def limpar_capturas_api(driver) -> None:
    try:
        driver.execute_script('window.__boloesApiCapturas = [];')
    except Exception:
        pass


def ler_capturas_api(driver) -> List[dict]:
    try:
        raw = driver.execute_script('return window.__boloesApiCapturas || [];')
        return raw if isinstance(raw, list) else []
    except Exception:
        return []


def _unwrap_node(data: Any) -> Any:
    if isinstance(data, dict):
        for chave in ('payload', 'dados', 'data', 'resultado'):
            inner = data.get(chave)
            if isinstance(inner, (dict, list)):
                return inner
    return data


def _item_atende_qtd_dezenas(item: dict, filtro_cfg) -> bool:
    if not filtro_cfg or getattr(filtro_cfg, 'qtd_dezenas', None) is None:
        return True
    esperado = int(filtro_cfg.qtd_dezenas)
    for chave in ('qtdNumeros', 'qtdDezenas', 'quantidadeNumeros', 'qtd_numeros'):
        val = item.get(chave)
        if val is not None:
            try:
                return int(val) == esperado
            except (TypeError, ValueError):
                pass
    return True


def _item_atende_loterica(item: dict, filtro_cfg) -> bool:
    if not filtro_cfg or not getattr(filtro_cfg, 'codigo', None):
        return True
    cod = str(filtro_cfg.codigo).strip()
    lot = item.get('loterica') or item.get('codigoLoterica') or item.get('idLoterica')
    if lot is not None and str(lot).strip() == cod:
        return True
    fmt = item.get('lotericaFormatada') or {}
    if isinstance(fmt, dict):
        if str(fmt.get('id') or '') == cod:
            return True
        digits = ''.join(ch for ch in str(fmt.get('codigo') or fmt.get('numeroFormatado') or '') if ch.isdigit())
        if cod in digits:
            return True
    nome = ' '.join(
        str(item.get(k) or '') for k in ('nomeFantasia', 'nomeRazaoSocial', 'nomeLoterica')
    )
    if cod in ''.join(ch for ch in nome if ch.isdigit()):
        return True
    return False


def _path_api_decodificado(url: str) -> str:
    u = url or ''
    ul = u.lower()
    if '/rest/v1/' not in ul:
        return ul
    seg = u.split('/rest/v1/')[-1].split('?')[0].strip('/')
    dec = decodificar_path_api(seg).lower()
    return dec if dec else ul


def _eh_url_detalhar(url: str) -> bool:
    u = _path_api_decodificado(url)
    return 'detalhar-bolao' in u or '/detalhar' in u


def _eh_url_lista_boloes(url: str) -> bool:
    u = _path_api_decodificado(url)
    return (
        'recuperar-boloes-disponiveis' in u
        or 'boloes-disponiveis' in u
        or 'recuperar-boloes' in u
    )


def _extrair_metadados_paginacao(data: Any) -> Optional[dict]:
    """Lê paginaAtual / ultimaPagina do payload da API de lista."""
    if not isinstance(data, dict):
        return None
    pilha = [data]
    vistos: set = set()
    while pilha:
        node = pilha.pop()
        if not isinstance(node, dict) or id(node) in vistos:
            continue
        vistos.add(id(node))
        pa = node.get('paginaAtual')
        up = node.get('ultimaPagina')
        if pa is not None and up is not None:
            try:
                return {
                    'pagina_atual': int(pa),
                    'ultima_pagina': int(up),
                    'total_registros': node.get('totalRegistros'),
                }
            except (TypeError, ValueError):
                pass
        for chave in ('payload', 'dados', 'data', 'resultado', 'mapa'):
            filho = node.get(chave)
            if isinstance(filho, dict):
                pilha.append(filho)
    return None


def ler_metadados_paginacao_api(driver) -> Optional[dict]:
    """Metadados da última resposta de lista (mais confiável que o botão Seguinte)."""
    melhor: Optional[dict] = None
    for cap in ler_capturas_api(driver):
        if not _eh_url_lista_boloes(cap.get('url') or ''):
            continue
        meta = _extrair_metadados_paginacao(cap.get('data'))
        if not meta:
            continue
        if melhor is None or meta['pagina_atual'] >= melhor['pagina_atual']:
            melhor = meta
    return melhor


def detectar_concurso_api(driver) -> Optional[str]:
    """Detecta o concurso a partir das capturas da API na página atual.

    Lê o campo 'concurso' ou 'numeroConcurso' do primeiro item encontrado
    nas capturas de detalhar-bolao ou recuperar-boloes-disponiveis.
    Retorna None se nenhum concurso for encontrado.
    """
    from boloes_api_parser import parse_bolao_api

    def _extrair_de_payload(payload: Any) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        val = payload.get('concurso') or payload.get('numeroConcurso')
        if val is None:
            return None
        digits = str(val).strip()
        return digits if digits.isdigit() else None

    for cap in ler_capturas_api(driver):
        url = (cap.get('url') or '').lower()
        data = cap.get('data')

        # 1) detalhar-bolao: mais confiável, payload é o bolão completo
        if 'detalhar-bolao' in url or '/detalhar' in url:
            b = parse_bolao_api(data)
            if b and b.get('concurso'):
                return b['concurso']
            # fallback: lê payload direto
            val = _extrair_de_payload(data)
            if val:
                return val

        # 2) recuperar-boloes-disponiveis: lista, cada item pode ter concurso
        if 'recuperar-boloes' in url or 'boloes-disponiveis' in url:
            if isinstance(data, dict):
                root = data
                for chave in ('payload', 'dados', 'data', 'resultado'):
                    if isinstance(root.get(chave), dict):
                        root = root[chave]
                        break
                if isinstance(root, dict):
                    val = _extrair_de_payload(root)
                    if val:
                        return val
                    # procura em listas dentro do dict
                    for chave in ('boloes', 'itens', 'lista', 'registros', 'dados'):
                        if isinstance(root.get(chave), list):
                            for item in root[chave]:
                                val = _extrair_de_payload(item)
                                if val:
                                    return val
            elif isinstance(data, list):
                for item in data:
                    val = _extrair_de_payload(item)
                    if val:
                        return val

    return None


def tem_mais_paginas_api(driver, pagina_processada: Optional[int] = None) -> Optional[bool]:
    """True/False se a API indicar; None se não houver metadados."""
    meta = ler_metadados_paginacao_api(driver)
    if not meta:
        return None
    atual = meta.get('pagina_atual') or pagina_processada or 0
    ultima = meta.get('ultima_pagina') or atual
    try:
        return int(atual) < int(ultima)
    except (TypeError, ValueError):
        return None


def contar_respostas_detalhar(driver) -> int:
    return sum(1 for cap in ler_capturas_api(driver) if _eh_url_detalhar(cap.get('url') or ''))


def _extrair_codigos_de_captura(
    cap: dict,
    filtro_cfg=None,
    max_itens: int = 55,
    filtrar_qtd: bool = False,
) -> List[str]:
    codigos: List[str] = []
    vistos: Set[str] = set()

    def registrar(item: dict) -> None:
        if len(codigos) >= max_itens:
            return
        if not _item_atende_loterica(item, filtro_cfg):
            return
        if filtrar_qtd and not _item_atende_qtd_dezenas(item, filtro_cfg):
            return
        cod = (item.get('codigoBolao') or item.get('codigo') or '').strip()
        if cod and cod not in vistos:
            vistos.add(cod)
            codigos.append(cod)

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 12 or len(codigos) >= max_itens:
            return
        if isinstance(node, dict):
            if node.get('codigoBolao'):
                registrar(node)
            for val in node.values():
                walk(val, depth + 1)
        elif isinstance(node, list):
            for item in node:
                walk(item, depth + 1)

    walk(_unwrap_node(cap.get('data')))
    walk(cap.get('data'))
    return codigos


def extrair_codigos_ultima_lista(
    driver,
    filtro_cfg=None,
    max_itens: int = 55,
    filtrar_qtd: bool = False,
) -> List[str]:
    """codigoBolao só da última resposta recuperar-boloes-disponiveis."""
    for cap in reversed(ler_capturas_api(driver)):
        if _eh_url_lista_boloes(cap.get('url') or ''):
            codigos = _extrair_codigos_de_captura(cap, filtro_cfg, max_itens, filtrar_qtd)
            if codigos:
                return codigos
    return extrair_codigos_bolao_capturas(driver, filtro_cfg, max_itens, filtrar_qtd)


_JS_SCROLL_LISTA = """
var callback = arguments[arguments.length - 1];
(function () {
  var step = Math.max(400, window.innerHeight * 0.75);
  var pos = 0;
  var lastH = 0;
  function maxH() {
    return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
  }
  function tick() {
    window.scrollTo(0, pos);
    var h = maxH();
    pos += step;
    if (pos >= h + 120 || (pos > h && h === lastH)) {
      window.scrollTo(0, 0);
      setTimeout(function () { callback(h); }, 400);
    } else {
      lastH = h;
      setTimeout(tick, 160);
    }
  }
  tick();
})();
"""


_JS_SCROLL_E_CONTAR_DETALHES = """
var callback = arguments[arguments.length - 1];
var RE_DETALHES = /detalh/i;
function visivel(el) {
  if (!el) return false;
  try {
    var st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
  } catch (e) {}
  return true;
}
function pushBtn(list, btn) {
  if (!btn || list.indexOf(btn) >= 0) return;
  list.push(btn);
}
function coletarBotoesDetalhes() {
  var out = [];
  var rowSels = [
    '[ng-repeat*="cota"]', '[ng-repeat*="Cota"]', '[ng-repeat*="bolao"]', '[ng-repeat*="Bolao"]',
    '.card', '[class*="bolao"]', '[class*="Bolao"]', 'tr[ng-repeat]', 'li[ng-repeat]'
  ];
  rowSels.forEach(function (sel) {
    document.querySelectorAll(sel).forEach(function (row) {
      if (!visivel(row)) return;
      var btnDet = null;
      row.querySelectorAll('button, a, [role="button"]').forEach(function (btn) {
        if (!visivel(btn)) return;
        var t = (btn.textContent || btn.innerText || '').trim();
        if (RE_DETALHES.test(t)) btnDet = btn;
      });
      if (btnDet) pushBtn(out, btnDet);
    });
  });
  document.querySelectorAll('button, a, [role="button"]').forEach(function (btn) {
    if (!visivel(btn)) return;
    var t = (btn.textContent || btn.innerText || '').trim();
    if (RE_DETALHES.test(t)) pushBtn(out, btn);
  });
  return out;
}
function scrollables() {
  var out = [];
  var seen = [];
  function add(el) {
    if (!el) return;
    for (var i = 0; i < seen.length; i++) if (seen[i] === el) return;
    seen.push(el);
    out.push(el);
  }
  add(document.documentElement);
  var prioSels = [
    '[class*="bolao"]', '[class*="Bolao"]', '.card', 'main', '[ng-view]',
    '.container-fluid', '.conteudo', '[class*="lista"]', '[class*="resultado"]'
  ];
  prioSels.forEach(function (sel) {
    document.querySelectorAll(sel).forEach(function (el) {
      try {
        var st = window.getComputedStyle(el);
        if ((st.overflowY === 'auto' || st.overflowY === 'scroll' || st.overflowY === 'overlay')
            && el.scrollHeight > el.clientHeight + 40) {
          add(el);
        }
      } catch (e) {}
      var p = el.parentElement;
      for (var d = 0; d < 5 && p; d++) {
        try {
          var st2 = window.getComputedStyle(p);
          if ((st2.overflowY === 'auto' || st2.overflowY === 'scroll' || st2.overflowY === 'overlay')
              && p.scrollHeight > p.clientHeight + 40) {
            add(p);
          }
        } catch (e) {}
        p = p.parentElement;
      }
    });
  });
  if (out.length >= 2) return out.slice(0, 6);
  var extra = [];
  document.querySelectorAll('div, section, article').forEach(function (el) {
    try {
      var st = window.getComputedStyle(el);
      if ((st.overflowY === 'auto' || st.overflowY === 'scroll')
          && el.scrollHeight > el.clientHeight + 120) {
        extra.push({ el: el, h: el.scrollHeight });
      }
    } catch (e) {}
  });
  extra.sort(function (a, b) { return b.h - a.h; });
  for (var ei = 0; ei < extra.length && out.length < 6; ei++) {
    add(extra[ei].el);
  }
  return out.slice(0, 6);
}
function scrollEl(el, onStep, t0) {
  return new Promise(function (resolve) {
    var isWin = (el === document.documentElement || el === document.body);
    var step = Math.max(320, isWin ? window.innerHeight * 0.72 : el.clientHeight * 0.68);
    var pos = 0;
    var lastH = 0;
    var ticks = 0;
    var MAX_TICKS = 22;
    function maxH() {
      if (isWin) return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
      return el.scrollHeight;
    }
    function applyScroll(p) {
      if (isWin) window.scrollTo(0, p);
      else el.scrollTop = p;
    }
    function tick() {
      ticks++;
      if (ticks > MAX_TICKS || (Date.now() - t0) > 16000) {
        applyScroll(0);
        resolve();
        return;
      }
      applyScroll(pos);
      if (onStep) onStep();
      var h = maxH();
      pos += step;
      if (pos >= h + 80 || (pos > h && h === lastH)) {
        applyScroll(0);
        setTimeout(function () { if (onStep) onStep(); resolve(); }, 180);
      } else {
        lastH = h;
        setTimeout(tick, 110);
      }
    }
    tick();
  });
}
(async function () {
  var todos = [];
  var t0 = Date.now();
  function snap() {
    coletarBotoesDetalhes().forEach(function (b) { pushBtn(todos, b); });
  }
  snap();
  var els = scrollables();
  for (var i = 0; i < els.length; i++) {
    if (Date.now() - t0 > 18000) break;
    await scrollEl(els[i], snap, t0);
  }
  window.scrollTo(0, 0);
  snap();
  callback(todos.length);
})();
"""


_JS_SCROLL_PASSO = """
var step = Math.max(320, window.innerHeight * 0.72);
window.scrollBy(0, step);
var alvo = null;
document.querySelectorAll('[class*="bolao"], .card, main, [ng-view]').forEach(function (el) {
  try {
    var st = window.getComputedStyle(el);
    if ((st.overflowY === 'auto' || st.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 40) {
      if (!alvo || el.scrollHeight > alvo.scrollHeight) alvo = el;
    }
  } catch (e) {}
});
if (alvo) alvo.scrollTop += step;
return true;
"""


_JS_LIMPAR_MARCAS_DET = """
document.querySelectorAll('[data-boloes-det-idx],[data-boloes-det-done]').forEach(function (el) {
  el.removeAttribute('data-boloes-det-idx');
  el.removeAttribute('data-boloes-det-done');
});
return true;
"""


_JS_MARCAR_PRIMEIROS_DET_DONE = """
var n = Math.max(0, parseInt(arguments[0], 10) || 0);
if (!n) return 0;
var RE_DETALHES = /detalh/i;
function visivel(el) {
  if (!el) return false;
  try {
    var st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
  } catch (e) {}
  return true;
}
function pushBtn(list, btn) {
  if (!btn || list.indexOf(btn) >= 0) return;
  list.push(btn);
}
var out = [];
var rowSels = [
  '[ng-repeat*="cota"]', '[ng-repeat*="Cota"]', '[ng-repeat*="bolao"]', '[ng-repeat*="Bolao"]',
  '.card', '[class*="bolao"]', '[class*="Bolao"]', 'tr[ng-repeat]', 'li[ng-repeat]'
];
rowSels.forEach(function (sel) {
  document.querySelectorAll(sel).forEach(function (row) {
    if (!visivel(row)) return;
    row.querySelectorAll('button, a, [role="button"]').forEach(function (btn) {
      if (!visivel(btn)) return;
      var t = (btn.textContent || btn.innerText || '').trim();
      if (RE_DETALHES.test(t)) pushBtn(out, btn);
    });
  });
});
document.querySelectorAll('button, a, [role="button"]').forEach(function (btn) {
  if (!visivel(btn)) return;
  var t = (btn.textContent || btn.innerText || '').trim();
  if (RE_DETALHES.test(t)) pushBtn(out, btn);
});
out.sort(function (a, b) {
  return a.getBoundingClientRect().top - b.getBoundingClientRect().top;
});
var marcados = 0;
for (var i = 0; i < out.length && marcados < n; i++) {
  out[i].setAttribute('data-boloes-det-done', '1');
  marcados++;
}
return marcados;
"""


_JS_COLETAR_BOTOES_DETALHES = """
var RE_DETALHES = /detalh/i;
var somentePendentes = !!arguments[0];
function visivel(el) {
  if (!el) return false;
  try {
    var st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
  } catch (e) {}
  return true;
}
function pushBtn(list, btn) {
  if (!btn || list.indexOf(btn) >= 0) return;
  if (somentePendentes && btn.getAttribute('data-boloes-det-done')) return;
  list.push(btn);
}
function coletarBotoesDetalhes() {
  var out = [];
  var rowSels = [
    '[ng-repeat*="cota"]', '[ng-repeat*="Cota"]', '[ng-repeat*="bolao"]', '[ng-repeat*="Bolao"]',
    '.card', '[class*="bolao"]', '[class*="Bolao"]', 'tr[ng-repeat]', 'li[ng-repeat]'
  ];
  rowSels.forEach(function (sel) {
    document.querySelectorAll(sel).forEach(function (row) {
      if (!visivel(row)) return;
      var btnDet = null;
      row.querySelectorAll('button, a, [role="button"]').forEach(function (btn) {
        if (!visivel(btn)) return;
        var t = (btn.textContent || btn.innerText || '').trim();
        if (RE_DETALHES.test(t)) btnDet = btn;
      });
      if (btnDet) pushBtn(out, btnDet);
    });
  });
  document.querySelectorAll('button, a, [role="button"]').forEach(function (btn) {
    if (!visivel(btn)) return;
    var t = (btn.textContent || btn.innerText || '').trim();
    if (RE_DETALHES.test(t)) pushBtn(out, btn);
  });
  out.sort(function (a, b) {
    return a.getBoundingClientRect().top - b.getBoundingClientRect().top;
  });
  return out;
}
return coletarBotoesDetalhes();
"""


_JS_CONTAR_ANGULAR_PAGINA = """
if (typeof angular === 'undefined') return 0;
var best = 0;
document.querySelectorAll('[ng-repeat], [ng-controller], .card, [class*="bolao"]').forEach(function (node) {
  try {
    var sc = angular.element(node).scope();
    for (var d = 0; d < 16 && sc; d++) {
      var lista = sc.cotas || sc.listaBoloes || sc.listaBoloesDisponiveis || sc.boloes
        || sc.items || (sc.vm && (sc.vm.cotas || sc.vm.listaBoloes || sc.vm.boloes));
      if (lista && lista.length > best) best = lista.length;
      sc = sc.$parent;
    }
  } catch (e) {}
});
return best;
"""


_JS_PEGAR_PROXIMO_BOTAO_DET = """
var RE_DETALHES = /detalh/i;
function visivel(el) {
  if (!el) return false;
  try {
    var st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
  } catch (e) {}
  return true;
}
function pushBtn(list, btn) {
  if (!btn || list.indexOf(btn) >= 0) return;
  if (btn.getAttribute('data-boloes-det-done')) return;
  list.push(btn);
}
function coletarPendentes() {
  var out = [];
  var rowSels = [
    '[ng-repeat*="cota"]', '[ng-repeat*="Cota"]', '[ng-repeat*="bolao"]', '[ng-repeat*="Bolao"]',
    '.card', '[class*="bolao"]', '[class*="Bolao"]', 'tr[ng-repeat]', 'li[ng-repeat]'
  ];
  rowSels.forEach(function (sel) {
    document.querySelectorAll(sel).forEach(function (row) {
      if (!visivel(row)) return;
      row.querySelectorAll('button, a, [role="button"]').forEach(function (btn) {
        if (!visivel(btn)) return;
        var t = (btn.textContent || btn.innerText || '').trim();
        if (RE_DETALHES.test(t)) pushBtn(out, btn);
      });
    });
  });
  document.querySelectorAll('button, a, [role="button"]').forEach(function (btn) {
    if (!visivel(btn)) return;
    var t = (btn.textContent || btn.innerText || '').trim();
    if (RE_DETALHES.test(t)) pushBtn(out, btn);
  });
  out.sort(function (a, b) { return a.getBoundingClientRect().top - b.getBoundingClientRect().top; });
  return out;
}
var candidatos = coletarPendentes();
if (!candidatos.length) return null;
var alvo = candidatos[0];
alvo.setAttribute('data-boloes-det-done', '1');
return alvo;
"""


_JS_CODIGOS_CARDS_VISIVEIS = """
var out = [];
function pushCod(c) {
  if (c && out.indexOf(c) < 0) out.push(c);
}
document.querySelectorAll('.card, [class*="bolao"]').forEach(function (card) {
  if (!card || card.offsetParent === null) return;
  var re = /detalh|ver |comprar|apostar|cotas|jogo/i;
  var temBtn = false;
  card.querySelectorAll('button').forEach(function (btn) {
    if (btn.offsetParent !== null && re.test(btn.textContent || '')) temBtn = true;
  });
  if (!temBtn) return;
  try {
    if (typeof angular !== 'undefined') {
      var sc = angular.element(card).scope();
      for (var d = 0; d < 12 && sc; d++) {
        var item = sc.cota || sc.item || sc.bolao || sc.$data;
        if (item && item.codigoBolao) { pushCod(item.codigoBolao); return; }
        var lista = sc.cotas || sc.listaBoloes || sc.boloes;
        if (lista && lista.length === 1 && lista[0].codigoBolao) { pushCod(lista[0].codigoBolao); return; }
        sc = sc.$parent;
      }
    }
  } catch (e) {}
});
return out;
"""


_JS_EXTRAIR_CODIGOS_ANGULAR = """
var codigoLoterica = arguments[0];
function atendeLot(item) {
  if (!codigoLoterica) return true;
  var lot = item.loterica || item.codigoLoterica;
  if (lot != null && String(lot) === String(codigoLoterica)) return true;
  var fmt = item.lotericaFormatada || {};
  if (fmt && (String(fmt.id) === String(codigoLoterica))) return true;
  return false;
}
function listaDeScope(sc) {
  for (var d = 0; d < 14 && sc; d++) {
    var lista = sc.cotas || sc.listaBoloes || sc.listaBoloesDisponiveis || sc.boloes
      || sc.items || (sc.vm && (sc.vm.cotas || sc.vm.listaBoloes || sc.vm.boloes));
    if (lista && lista.length && lista[0] && lista[0].codigoBolao) {
      var out = [];
      for (var i = 0; i < lista.length; i++) {
        if (lista[i] && lista[i].codigoBolao && atendeLot(lista[i])) out.push(lista[i].codigoBolao);
      }
      if (out.length) return out;
    }
    sc = sc.$parent;
  }
  return [];
}
if (typeof angular === 'undefined') return [];
var nodes = document.querySelectorAll('[ng-repeat], .card, [class*="bolao"], [ng-controller]');
for (var ni = 0; ni < nodes.length; ni++) {
  try {
    var sc = angular.element(nodes[ni]).scope();
    var codigos = listaDeScope(sc);
    if (codigos.length) return codigos;
  } catch (e) {}
}
return [];
"""


def _scroll_lista_completa(driver) -> None:
    """Rola a janela (rapido) para revelar cards abaixo."""
    try:
        driver.set_script_timeout(25)
        driver.execute_async_script(_JS_SCROLL_LISTA)
    except Exception:
        try:
            driver.execute_script(
                'window.scrollTo(0, document.body.scrollHeight);'
                'window.scrollTo(0, 0);'
            )
        except Exception:
            pass
    time.sleep(0.35)


def _scroll_e_contar_detalhes(
    driver,
    log_fn: Optional[Callable[[str], None]] = None,
) -> int:
    """Rola lista (janela + painel de boloes) e retorna total de botoes Detalhes."""
    if log_fn:
        log_fn('  [TELA] Rolando lista e contando botoes Detalhes (aguarde)...')
    try:
        driver.set_script_timeout(25)
        n = int(driver.execute_async_script(_JS_SCROLL_E_CONTAR_DETALHES) or 0)
        if log_fn:
            log_fn(f'  [TELA] Contagem apos rolagem: {n} botao(oes) Detalhes.')
        if n > 0:
            return n
    except Exception as exc:
        if log_fn:
            log_fn(f'  [TELA] Rolagem completa falhou ({exc}) — tentando rapido...')
    _scroll_lista_completa(driver)
    try:
        n = len(driver.execute_script(_JS_COLETAR_BOTOES_DETALHES, False) or [])
        if log_fn:
            log_fn(f'  [TELA] Contagem rapida: {n} botao(oes) Detalhes.')
        return n
    except Exception:
        return 0


def _scroll_passo_lista(driver) -> None:
    """Um passo de rolagem (janela + paineis scrollaveis)."""
    try:
        driver.execute_script(_JS_SCROLL_PASSO)
    except Exception:
        try:
            driver.execute_script('window.scrollBy(0, Math.max(350, window.innerHeight * 0.75));')
        except Exception:
            pass
    time.sleep(0.45)


def limpar_marcas_detalhes_pagina(driver) -> None:
    try:
        driver.execute_script(_JS_LIMPAR_MARCAS_DET)
    except Exception:
        pass


def marcar_detalhes_ja_extraidos(
    driver,
    qtd: int,
    log_fn: Optional[Callable[[str], None]] = None,
) -> int:
    """
    Marca os primeiros `qtd` botões Detalhes (ordem visual) como já processados.
    Usado na retomada: registros já no JSON desta página → não reclicar.
    """
    n = max(0, int(qtd or 0))
    if n <= 0:
        return 0
    try:
        marcados = int(driver.execute_script(_JS_MARCAR_PRIMEIROS_DET_DONE, n) or 0)
    except Exception:
        marcados = 0
    if log_fn and marcados:
        log_fn(
            f'  [CHECKPOINT] {marcados} Detalhes já no JSON — '
            f'pulando; próximo clique a partir do #{marcados + 1}.'
        )
    return marcados


def preparar_pagina_para_detalhes(
    driver,
    log_fn: Optional[Callable[[str], None]] = None,
) -> None:
    """Nova pagina / novo filtro: limpa marcas e rola lista antes de contar."""
    if log_fn:
        log_fn('  [TELA] Preparando pagina (limpar marcas + rolar lista)...')
    limpar_marcas_detalhes_pagina(driver)
    _scroll_lista_completa(driver)


def contar_detalhes_pagina(
    driver,
    preparar: bool = True,
    log_fn: Optional[Callable[[str], None]] = None,
) -> int:
    """Quantidade de botoes Detalhes na pagina (apos rolar lista inteira)."""
    if preparar:
        limpar_marcas_detalhes_pagina(driver)
        n_dom = _scroll_e_contar_detalhes(driver, log_fn)
    else:
        n_dom = 0
        try:
            n_dom = len(driver.execute_script(_JS_COLETAR_BOTOES_DETALHES, False) or [])
        except Exception:
            pass
        if n_dom <= 6:
            n_dom = max(n_dom, _scroll_e_contar_detalhes(driver, log_fn))
    n_ang = 0
    try:
        n_ang = int(driver.execute_script(_JS_CONTAR_ANGULAR_PAGINA) or 0)
    except Exception:
        pass
    return max(n_dom, n_ang)


def contar_botoes_bolao_visiveis(driver) -> int:
    """Alias — use contar_detalhes_pagina."""
    return contar_detalhes_pagina(driver)


def extrair_codigos_cards_visiveis(driver, max_itens: int = 55) -> List[str]:
    try:
        raw = driver.execute_script(_JS_CODIGOS_CARDS_VISIVEIS)
        if isinstance(raw, list):
            out = [str(c).strip() for c in raw if c]
            return out[:max_itens]
    except Exception:
        pass
    return []


def extrair_codigos_angular_lista(driver, filtro_cfg=None, max_itens: int = 55) -> List[str]:
    cod_lot = ''
    if filtro_cfg and getattr(filtro_cfg, 'codigo', None):
        cod_lot = str(filtro_cfg.codigo).strip()
    try:
        raw = driver.execute_script(_JS_EXTRAIR_CODIGOS_ANGULAR, cod_lot)
        if isinstance(raw, list):
            out: List[str] = []
            vistos: Set[str] = set()
            for c in raw:
                cod = str(c or '').strip()
                if cod and cod not in vistos:
                    vistos.add(cod)
                    out.append(cod)
                if len(out) >= max_itens:
                    break
            return out
    except Exception:
        pass
    return []


def detectar_detalhes_pagina(
    driver,
    filtro_cfg=None,
    max_itens: int = 55,
    preparar: bool = True,
    log_fn: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Meta da pagina = botoes Detalhes (apos rolar lista)."""
    n_detalhes = contar_detalhes_pagina(driver, preparar=preparar, log_fn=log_fn)
    codigos = extrair_codigos_cards_visiveis(driver, max_itens)
    if n_detalhes and len(codigos) > n_detalhes:
        codigos = codigos[:n_detalhes]

    return {
        'n_esperado': n_detalhes,
        'n_detalhes': n_detalhes,
        'codigos': codigos,
    }


def resolver_contagem_pagina(driver, filtro_cfg=None, max_itens: int = 55) -> Dict[str, Any]:
    """Alias legado — mesma regra: contar Detalhes na tela."""
    return detectar_detalhes_pagina(driver, filtro_cfg, max_itens)


def aguardar_detalhes_visiveis(
    driver,
    minimo: int = 1,
    timeout: float = 12.0,
    log_fn: Optional[Callable[[str], None]] = None,
) -> int:
    """Espera haver pelo menos `minimo` botoes Detalhes na pagina."""
    fim = time.time() + timeout
    preparou = False
    while time.time() < fim:
        n = contar_detalhes_pagina(driver, preparar=not preparou, log_fn=log_fn if not preparou else None)
        preparou = True
        if n >= minimo:
            return n
        time.sleep(0.45)
    return contar_detalhes_pagina(driver, preparar=False)


def codigos_bolao_pendentes(
    codigos: List[str],
    offset: int = 0,
) -> List[str]:
    """Fatia da lista de codigos já definida para esta página."""
    if offset >= len(codigos):
        return []
    return codigos[offset:]


def extrair_codigos_bolao_capturas(
    driver,
    filtro_cfg=None,
    max_itens: int = 40,
    filtrar_qtd: bool = False,
) -> List[str]:
    """codigoBolao acumulado nas capturas (legado — prefira extrair_codigos_ultima_lista)."""
    codigos: List[str] = []
    vistos: Set[str] = set()
    for cap in ler_capturas_api(driver):
        if _eh_url_detalhar(cap.get('url') or ''):
            continue
        for cod in _extrair_codigos_de_captura(cap, filtro_cfg, max_itens, filtrar_qtd):
            if cod not in vistos:
                vistos.add(cod)
                codigos.append(cod)
            if len(codigos) >= max_itens:
                break
        if len(codigos) >= max_itens:
            break

    if not codigos and filtrar_qtd and filtro_cfg and getattr(filtro_cfg, 'qtd_dezenas', None):
        return extrair_codigos_bolao_capturas(
            driver, filtro_cfg, max_itens, filtrar_qtd=False,
        )

    return codigos


def aguardar_codigos_lista(
    driver,
    filtro_cfg=None,
    minimo: int = 3,
    timeout: float = 14.0,
) -> int:
    """Espera captura da lista (recuperar-boloes-disponiveis) com codigoBolao."""
    fim = time.time() + timeout
    while time.time() < fim:
        n = len(extrair_codigos_bolao_capturas(driver, filtro_cfg, 60, filtrar_qtd=False))
        if n >= minimo:
            return n
        time.sleep(0.45)
    return len(extrair_codigos_bolao_capturas(driver, filtro_cfg, 60, filtrar_qtd=False))


_JS_MODO_SILENCIOSO = """
(function (ativar) {
  var id = 'boloes-api-silent-style';
  var obsKey = '__boloesSilentObs';
  var el = document.getElementById(id);

  function esconderDialogos() {
    /* NÃO usar [class*="modal"] — casa com "modalidade" e quebra a lista */
    var sels = [
      '.modal', '.modal-backdrop', '.modal-dialog', '.modal-content',
      '.modal.fade', '.modal.show', '.modal.in',
      '[role="dialog"]', '[aria-modal="true"]',
      '.cdk-overlay-container', '.cdk-overlay-backdrop', '.cdk-overlay-pane',
      '.mat-dialog-container', '.mat-mdc-dialog-container',
      '.ui-dialog', '.ui-widget-overlay',
      '.fancybox-container', '.fancybox-overlay',
      '[class*="popup-bolao"]', '[class*="PopupBolao"]',
      '[class*="detalhe-bolao"]', '[class*="DetalheBolao"]',
      '[id*="modalBolao"]', '[id*="ModalBolao"]', '[id*="modal-bolao"]'
    ];
    sels.forEach(function (sel) {
      try {
        document.querySelectorAll(sel).forEach(function (node) {
          if (!node || node.getAttribute('data-boloes-silent-skip')) return;
          node.style.setProperty('display', 'none', 'important');
          node.style.setProperty('opacity', '0', 'important');
          node.style.setProperty('visibility', 'hidden', 'important');
          node.style.setProperty('pointer-events', 'none', 'important');
          node.setAttribute('data-boloes-silent-hid', '1');
        });
      } catch (e) {}
    });
  }

  if (ativar) {
    if (!el) {
      el = document.createElement('style');
      el.id = id;
      document.head.appendChild(el);
    }
    /* Selectors específicos — SEM [class*="modal"] (quebra "modalidade") */
    el.textContent = [
      '.modal, .modal-backdrop, .modal-dialog, .modal-content,',
      '.modal.fade, .modal.show, .modal.in,',
      '[role="dialog"], [aria-modal="true"],',
      '.cdk-overlay-container, .cdk-overlay-backdrop, .cdk-overlay-pane,',
      '.mat-dialog-container, .mat-mdc-dialog-container,',
      '.ui-dialog, .ui-widget-overlay, .fancybox-container, .fancybox-overlay,',
      '[class*="popup-bolao"], [class*="PopupBolao"],',
      '[class*="detalhe-bolao"], [class*="DetalheBolao"],',
      '[id*="modalBolao"], [id*="ModalBolao"], [id*="modal-bolao"]',
      '{ display:none!important; opacity:0!important; visibility:hidden!important;',
      '  pointer-events:none!important; z-index:-9999!important; }',
      'body.modal-open { overflow:auto!important; padding-right:0!important; }'
    ].join(' ');
    esconderDialogos();
    if (!window[obsKey]) {
      try {
        window[obsKey] = new MutationObserver(function () { esconderDialogos(); });
        window[obsKey].observe(document.documentElement, { childList: true, subtree: true });
      } catch (e) {}
    }
  } else {
    if (el) el.remove();
    if (window[obsKey]) {
      try { window[obsKey].disconnect(); } catch (e) {}
      window[obsKey] = null;
    }
    try {
      document.querySelectorAll('[data-boloes-silent-hid]').forEach(function (node) {
        node.removeAttribute('data-boloes-silent-hid');
        node.style.removeProperty('display');
        node.style.removeProperty('opacity');
        node.style.removeProperty('visibility');
        node.style.removeProperty('pointer-events');
      });
    } catch (e) {}
  }
})(arguments[0]);
"""


_JS_DETALHAR_UM_ASYNC = """
var codigo = arguments[0] || '';
var indice = arguments[1] || 0;
var callback = arguments[arguments.length - 1];

(function () {
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  function listaDeScope(sc) {
    for (var d = 0; d < 14 && sc; d++) {
      var lista = sc.cotas || sc.listaBoloes || sc.listaBoloesDisponiveis || sc.boloes
        || sc.items || (sc.vm && (sc.vm.cotas || sc.vm.listaBoloes || sc.vm.boloes));
      if (lista && lista.length && lista[0] && lista[0].codigoBolao) return { scope: sc, lista: lista };
      sc = sc.$parent;
    }
    return null;
  }

  function acharContexto() {
    if (typeof angular === 'undefined') return null;
    var nodes = document.querySelectorAll('[ng-repeat], .card, [class*="bolao"], [ng-controller]');
    for (var ni = 0; ni < nodes.length; ni++) {
      try {
        var ctx = listaDeScope(angular.element(nodes[ni]).scope());
        if (ctx) return ctx;
      } catch (e) {}
    }
    return null;
  }

  (async function () {
    var antes = (window.__boloesApiCapturas || []).length;
    var ctx = acharContexto();
    if (!ctx) {
      callback({ ok: 0, via: '', motivo: 'sem-angular' });
      return;
    }

    var item = null;
    if (codigo) {
      for (var i = 0; i < ctx.lista.length; i++) {
        if (String(ctx.lista[i].codigoBolao) === String(codigo)) { item = ctx.lista[i]; break; }
      }
    }
    if (!item && ctx.lista[indice]) item = ctx.lista[indice];
    if (!item) {
      callback({ ok: 0, via: '', motivo: 'sem-item' });
      return;
    }

    var arg = item.codigoBolao || item;
    var inj = null;
    try { inj = angular.element(document.querySelector('[ng-app]') || document.body).injector(); } catch (e) {}

    var svcs = ['bolaoCaixaService', 'BolaoCaixaService', 'bolaoService', 'BolaoService', 'bolaoCaixaRestService'];
    var metodos = ['detalharBolao', 'detalhar', 'getDetalheBolao', 'recuperarDetalheBolao'];

    if (inj) {
      for (var si = 0; si < svcs.length; si++) {
        var svc = null;
        try { svc = inj.get(svcs[si]); } catch (e) { continue; }
        if (!svc) continue;
        for (var mj = 0; mj < metodos.length; mj++) {
          if (typeof svc[metodos[mj]] !== 'function') continue;
          try {
            var ret = svc[metodos[mj]].call(svc, arg);
            if (ret && typeof ret.then === 'function') await ret.catch(function () {});
            await sleep(120);
            callback({
              ok: 1,
              via: svcs[si] + '.' + metodos[mj],
              capturasNovas: (window.__boloesApiCapturas || []).length - antes,
              codigo: String(item.codigoBolao || ''),
            });
            return;
          } catch (e) {}
        }
      }
    }

    var fns = ['detalharBolao', 'detalhar', 'abrirDetalhes', 'verDetalhes', 'detalhesBolao'];
    for (var fi = 0; fi < fns.length; fi++) {
      if (typeof ctx.scope[fns[fi]] !== 'function') continue;
      try {
        var ret2 = ctx.scope[fns[fi]](item);
        if (ret2 && typeof ret2.then === 'function') await ret2.catch(function () {});
        await sleep(120);
        callback({
          ok: 1,
          via: 'scope.' + fns[fi],
          capturasNovas: (window.__boloesApiCapturas || []).length - antes,
          codigo: String(item.codigoBolao || ''),
        });
        return;
      } catch (e) {}
    }

    callback({ ok: 0, via: '', motivo: 'sem-metodo' });
  })();
})();
"""


def _modo_silencioso(driver, ativar: bool) -> None:
    try:
        driver.execute_script(_JS_MODO_SILENCIOSO, bool(ativar))
    except Exception:
        pass


def _garantir_modo_visivel(driver) -> None:
    """Remove CSS que ocultava modal — extracao com popup visivel."""
    _modo_silencioso(driver, False)
    try:
        driver.execute_script(
            "var el=document.getElementById('boloes-api-silent-style'); if(el) el.remove();"
        )
    except Exception:
        pass


def disparar_detalhes_visivel(
    driver,
    log_fn: Optional[Callable[[str], None]] = None,
    max_itens: int = 55,
    offset: int = 0,
    n_total: int = 0,
) -> int:
    """[1] — clique Detalhes com popup VISÍVEL."""
    return disparar_detalhes(
        driver, log_fn=log_fn, max_itens=max_itens, offset=offset, n_total=n_total,
        sem_popup=False,
    )


def _barra_progresso(atual: int, total: int, prefix: str = '[API]') -> str:
    total = max(int(total or 0), 1)
    atual = max(0, min(int(atual or 0), total))
    pct = 100.0 * atual / total
    largura = 20
    cheios = int(round(largura * pct / 100.0))
    barra = '█' * cheios + '░' * (largura - cheios)
    return f'  {prefix} {barra} {atual}/{total} ({pct:.0f}%)'


def _log_progresso(
    log_fn: Optional[Callable[[str], None]],
    atual: int,
    total: int,
    *,
    prefix: str = '[API]',
    extra: str = '',
) -> None:
    if not log_fn:
        return
    msg = _barra_progresso(atual, total, prefix)
    if extra:
        msg = f'{msg}  {extra}'
    log_fn(msg)


def _disparar_via_api_endpoint(
    driver,
    codigos: Optional[List[str]],
    max_itens: int,
    offset: int,
    log_fn: Optional[Callable[[str], None]] = None,
    n_total: int = 0,
) -> int:
    """
    [C] — chama o endpoint detalhar-bolao via serviço Angular do site
    (gera o ?q= criptografado). Sem abrir popup. 1 bolão por vez = barra anda.
    """
    _modo_silencioso(driver, True)
    lista = list(codigos or [])
    if offset and lista:
        lista = lista[offset:]
    limite = max(int(max_itens or 1), 1)
    if lista:
        alvos: List[Optional[str]] = lista[:limite]
    else:
        alvos = [None] * limite

    total_barra = max(int(n_total or 0), len(alvos), 1)
    if log_fn:
        log_fn('  [API] Baixando via endpoint detalhar-bolao (SEM popup)...')
        _log_progresso(log_fn, 0, total_barra, extra='iniciando')

    try:
        driver.set_script_timeout(10)
    except Exception:
        pass

    ok = 0
    via_ok = ''
    falhas_seguidas = 0
    for i, cod in enumerate(alvos):
        _modo_silencioso(driver, True)
        try:
            res = driver.execute_async_script(_JS_DETALHAR_UM_ASYNC, cod or '', offset + i)
        except Exception as exc:
            falhas_seguidas += 1
            if log_fn and falhas_seguidas <= 2:
                log_fn(f'  [API] Falha item {i + 1}: {exc}')
            if falhas_seguidas >= 3 and ok == 0:
                break
            continue

        if isinstance(res, dict) and int(res.get('ok') or 0) > 0:
            ok += 1
            falhas_seguidas = 0
            via_ok = str(res.get('via') or via_ok)
            marcar_detalhes_ja_extraidos(driver, offset + ok, None)
            if log_fn and (ok == 1 or ok % 2 == 0 or ok >= total_barra):
                _log_progresso(log_fn, ok, total_barra, extra=via_ok or 'detalhar-bolao')
        else:
            falhas_seguidas += 1
            motivo = ''
            if isinstance(res, dict):
                motivo = str(res.get('motivo') or '')
            if falhas_seguidas >= 3 and ok == 0:
                if log_fn:
                    log_fn(f'  [API] Endpoint indisponível ({motivo or "sem-metodo"}) — fallback clique oculto.')
                break

    if ok and log_fn:
        _explicar_via_js(via_ok, log_fn)
        _log_progresso(log_fn, ok, max(total_barra, ok), extra='API ok')
        log_fn(f'  [API] {ok} bolão(ões) via endpoint — aguardando JSON...')
    time.sleep(0.5 if ok else 0.1)
    return ok


def disparar_detalhes(
    driver,
    log_fn: Optional[Callable[[str], None]] = None,
    max_itens: int = 55,
    offset: int = 0,
    n_total: int = 0,
    *,
    sem_popup: bool = False,
    manter_oculto: bool = True,
    codigos: Optional[List[str]] = None,
) -> int:
    """
    Fluxo de Detalhes ([1] e [C] iguais).

    sem_popup=False → [1] clique + popup visível
    sem_popup=True  → [C] API/endpoint primeiro; se falhar, mesmo clique com popup oculto
    """
    if sem_popup:
        n_api = _disparar_via_api_endpoint(
            driver, codigos, max_itens=max_itens, offset=offset,
            log_fn=log_fn, n_total=n_total,
        )
        if n_api > 0:
            if not manter_oculto:
                _modo_silencioso(driver, False)
            return n_api
        if log_fn:
            log_fn('  [API] Fallback: mesmo clique do [1], popup OCULTO.')

    if sem_popup:
        _modo_silencioso(driver, True)
    else:
        _garantir_modo_visivel(driver)

    if offset == 0:
        _scroll_lista_completa(driver)

    if log_fn and n_total:
        modo = 'SEM POPUP' if sem_popup else 'popup visível'
        log_fn(f'  [TELA] Meta: {n_total} Detalhes nesta pagina ({modo}).')

    cliques = 0
    sem_progresso = 0
    limite = max_itens if max_itens > 0 else 99

    while cliques < limite:
        if sem_popup:
            _modo_silencioso(driver, True)

        btn = None
        try:
            btn = driver.execute_script(_JS_PEGAR_PROXIMO_BOTAO_DET)
        except Exception:
            pass

        if not btn:
            try:
                _scroll_passo_lista(driver)
                if sem_popup:
                    _modo_silencioso(driver, True)
                btn = driver.execute_script(_JS_PEGAR_PROXIMO_BOTAO_DET)
            except Exception:
                pass

        if not btn:
            sem_progresso += 1
            if sem_progresso >= 8:
                break
            time.sleep(0.4)
            continue

        sem_progresso = 0
        num = offset + cliques + 1
        if log_fn:
            rotulo = f'{num}/{n_total}' if n_total else str(num)
            if sem_popup:
                _log_progresso(log_fn, cliques + 1, max(n_total, limite), prefix='[RÁPIDO]', extra=f'Detalhes {rotulo}')
            else:
                log_fn(f'  [TELA] >>> Clicando Detalhes {rotulo} (voce vera o popup)...')

        try:
            driver.execute_script('arguments[0].scrollIntoView({block:"center"});', btn)
            time.sleep(0.4)
            try:
                btn.click()
            except Exception:
                driver.execute_script('arguments[0].click();', btn)
            cliques += 1
            time.sleep(1.15)
            if sem_popup:
                _modo_silencioso(driver, True)
                _fechar_popup_rapido(driver, forcar_escape=True)
            else:
                _fechar_popup_rapido(driver)
            time.sleep(0.4)
        except Exception as exc:
            if log_fn:
                log_fn(f'  [TELA] Falha no clique Detalhes #{num}: {exc}')

    if sem_popup and not manter_oculto:
        _modo_silencioso(driver, False)

    if log_fn:
        if cliques:
            log_fn(f'  [TELA] {cliques} clique(s) Detalhes nesta rodada.')
        elif n_total:
            log_fn('  [TELA] Nenhum Detalhes pendente encontrado.')
    return cliques


def disparar_detalhes_via_js(
    driver,
    filtro_cfg=None,
    log_fn: Optional[Callable[[str], None]] = None,
    max_itens: int = 55,
    offset_codigos: int = 0,
    codigos_pagina: Optional[List[str]] = None,
) -> int:
    """Alias → [C] API/endpoint."""
    del filtro_cfg
    return disparar_detalhes(
        driver, log_fn=log_fn, max_itens=max_itens, offset=offset_codigos,
        n_total=0, sem_popup=True, manter_oculto=True, codigos=codigos_pagina,
    )


def disparar_detalhes_oculto(
    driver,
    log_fn: Optional[Callable[[str], None]] = None,
    max_itens: int = 55,
    offset: int = 0,
    n_total: int = 0,
    *,
    manter_oculto: bool = True,
    codigos: Optional[List[str]] = None,
) -> int:
    """Alias → [C] API/endpoint (+ clique oculto se precisar)."""
    return disparar_detalhes(
        driver, log_fn=log_fn, max_itens=max_itens, offset=offset, n_total=n_total,
        sem_popup=True, manter_oculto=manter_oculto, codigos=codigos,
    )


def disparar_detalhes_sem_popup(
    driver,
    log_fn: Optional[Callable[[str], None]] = None,
    filtro_cfg=None,
    max_itens: int = 55,
    offset_codigos: int = 0,
    codigos_pagina: Optional[List[str]] = None,
    n_total: int = 0,
    *,
    forcar_sem_popup: bool = False,
) -> int:
    """
    forcar_sem_popup=False → [1] clique + popup
    forcar_sem_popup=True  → [C] API detalhar-bolao (sem popup)
    """
    del filtro_cfg
    return disparar_detalhes(
        driver, log_fn=log_fn, max_itens=max_itens, offset=offset_codigos,
        n_total=n_total, sem_popup=bool(forcar_sem_popup), manter_oculto=True,
        codigos=codigos_pagina,
    )


def detalhar_pagina_ate_esperado(
    driver,
    filtro_cfg,
    parser_slug: str,
    hashes_vistos: Set[str],
    n_esperado: int,
    codigos_pagina: List[str],
    log_fn: Optional[Callable[[str], None]] = None,
    max_rodadas: int = 15,
    on_progresso: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
    ja_coletados: int = 0,
    *,
    sem_popup: bool = False,
) -> List[Dict[str, Any]]:
    """
    Clica em todos os Detalhes da pagina (rodadas ate bater meta ou esgotar botoes).

    sem_popup=True → [C]: baixa via API detalhar-bolao (sem popup); fallback = clique oculto.
    """
    hashes_inicio = len(hashes_vistos)
    boloes_novos: List[Dict[str, Any]] = []
    estagnacao = 0
    meta = n_esperado
    previos = max(0, int(ja_coletados or 0))

    if previos and meta and previos >= meta:
        if log_fn:
            log_fn(
                f'  [CHECKPOINT] Página já completa no JSON '
                f'({previos}/{meta}) — sem reclicar Detalhes.'
            )
        return boloes_novos

    if previos:
        marcar_detalhes_ja_extraidos(driver, previos, log_fn)

    if sem_popup:
        _modo_silencioso(driver, True)
        if log_fn:
            log_fn('  [RÁPIDO] CSS anti-popup ATIVO nesta página (até o fim).')

    try:
        for rodada in range(1, max_rodadas + 1):
            if sem_popup:
                _modo_silencioso(driver, True)

            chunk = coletar_boloes_das_capturas(
                driver, hashes_vistos, log_fn, filtro_cfg, parser_slug, filtrar_dezenas=False,
            )
            boloes_novos.extend(chunk)
            n_ok_pagina = previos + (len(hashes_vistos) - hashes_inicio)

            # Callback de progresso (salvar em tempo real)
            if on_progresso and chunk:
                on_progresso(boloes_novos)

            n_tela = contar_detalhes_pagina(driver, preparar=False, log_fn=log_fn)
            if n_tela > meta:
                meta = n_tela
                if log_fn:
                    log_fn(f'  [TELA] Contagem atualizada: {meta} botao(oes) Detalhes.')

            if meta and n_ok_pagina >= meta:
                if log_fn:
                    log_fn(f'  [TELA] Completo: {n_ok_pagina}/{meta} bolao(oes) nesta pagina.')
                break

            pendentes = max(0, meta - n_ok_pagina) if meta else 99
            pendentes_btn = 0
            try:
                pendentes_btn = len(driver.execute_script(_JS_COLETAR_BOTOES_DETALHES, True) or [])
            except Exception:
                pass

            if pendentes_btn == 0 and meta and n_ok_pagina >= meta:
                break
            if pendentes_btn == 0 and not meta:
                break
            # Já cobertos no JSON + sem botão pendente → não insiste em reclicar
            if pendentes_btn == 0 and previos and n_ok_pagina >= previos:
                if log_fn:
                    log_fn(
                        f'  [CHECKPOINT] Sem Detalhes pendentes na tela '
                        f'(coletados={n_ok_pagina}/{meta or "?"}).'
                    )
                break

            if log_fn:
                modo = 'SEM POPUP' if sem_popup else 'VISÍVEL'
                log_fn(
                    f'  [TELA] Rodada {rodada} [{modo}]: meta={meta} | coletados={n_ok_pagina} | '
                    f'faltam={pendentes} | botoes_pendentes={pendentes_btn}'
                )

            if pendentes <= 0 and meta:
                break

            n_disp = disparar_detalhes_sem_popup(
                driver, log_fn, filtro_cfg, max_itens=max(pendentes, pendentes_btn, 1),
                offset_codigos=n_ok_pagina, codigos_pagina=codigos_pagina,
                n_total=meta or n_tela,
                forcar_sem_popup=sem_popup,
            )
            if log_fn and sem_popup:
                log_fn('  [RÁPIDO] Coletando JSON das capturas API...')
            aguardar_capturas_api(driver, minimo=1, timeout=8 if sem_popup else 12)

            # Coleta parcial já nesta rodada (grava mais cedo via on_progresso)
            chunk_mid = coletar_boloes_das_capturas(
                driver, hashes_vistos, log_fn, filtro_cfg, parser_slug, filtrar_dezenas=False,
            )
            if chunk_mid:
                boloes_novos.extend(chunk_mid)
                n_ok_pagina = previos + (len(hashes_vistos) - hashes_inicio)
                if on_progresso:
                    on_progresso(boloes_novos)
                if log_fn:
                    if sem_popup:
                        _log_progresso(
                            log_fn, n_ok_pagina, meta or max(n_ok_pagina, 1),
                            prefix='[SAVE]',
                            extra=f'+{len(chunk_mid)} no JSON parcial',
                        )
                    else:
                        log_fn(f'  [SAVE] Parcial: {n_ok_pagina}/{meta or "?"} nesta página (+{len(chunk_mid)})')

            if n_disp == 0:
                estagnacao += 1
            else:
                estagnacao = 0

            if estagnacao >= 3:
                if log_fn:
                    log_fn('  [TELA] Sem mais Detalhes para clicar nesta pagina.')
                break

            if meta and n_ok_pagina >= meta:
                if log_fn:
                    log_fn(f'  [TELA] Completo: {n_ok_pagina}/{meta} bolao(oes) nesta pagina.')
                break

        chunk = coletar_boloes_das_capturas(
            driver, hashes_vistos, log_fn, filtro_cfg, parser_slug, filtrar_dezenas=False,
        )
        boloes_novos.extend(chunk)
        if on_progresso and chunk:
            on_progresso(boloes_novos)
        return boloes_novos
    finally:
        if sem_popup:
            _modo_silencioso(driver, False)
            if log_fn:
                log_fn('  [RÁPIDO] CSS anti-popup desligado (fim da página).')


def _extrair_boloes_de_captura(data: Any, parser_slug: str = '') -> List[Dict[str, Any]]:
    if not data:
        return []
    boloes = extrair_todos_boloes_json(data, somente_com_dezenas=True, parser_slug_hint=parser_slug)
    if boloes:
        return boloes
    return parse_lista_boloes_api(data, parser_slug_hint=parser_slug)


def _fechar_popup_rapido(driver, forcar_escape: bool = False) -> None:
    try:
        if not forcar_escape:
            for sel in (
                "button.close, .btn-close, [class*='close']",
                "button[aria-label='Close']",
            ):
                for btn in driver.find_elements(By.CSS_SELECTOR, sel):
                    if btn.is_displayed():
                        btn.click()
                        time.sleep(0.25)
                        return
        driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
        time.sleep(0.2 if forcar_escape else 0.25)
    except Exception:
        pass


def disparar_detalhes_api_pagina(
    driver,
    log_fn: Optional[Callable[[str], None]] = None,
    max_cliques: int = 55,
    silencioso: bool = False,
    manter_oculto: bool = False,
) -> int:
    """
    Clica em Detalhes de cada card — dispara boloes/detalhar-bolao na API.
    silencioso=True: modal oculto via CSS (SEM popup) + barra.
    manter_oculto=True: NÃO remove o CSS ao terminar (deixe a página gerenciar).
    """
    if silencioso:
        _modo_silencioso(driver, True)

    driver.execute_script('window.scrollTo(0, document.body.scrollHeight);')
    time.sleep(0.35 if silencioso else 0.8)
    driver.execute_script('window.scrollTo(0, 0);')
    time.sleep(0.2 if silencioso else 0.5)

    seletores = [
        'button.btn-primary',
        'button.btn-success',
        "button[class*='btn'][class*='primary']",
        "button[class*='btn'][class*='success']",
        '.card button',
        "[class*='bolao'] button",
        "button[class*='apostar']",
        "button[class*='comprar']",
        "button[class*='ver']",
    ]
    palavras = ('ver', 'detalh', 'comprar', 'apostar', 'cotas', 'jogo')
    botoes: List = []
    for sel in seletores:
        try:
            for btn in driver.find_elements(By.CSS_SELECTOR, sel):
                if btn in botoes or not btn.is_displayed():
                    continue
                txt = (btn.text or '').lower()
                if any(p in txt for p in palavras):
                    botoes.append(btn)
        except Exception:
            pass

    alvo = botoes[:max_cliques]
    total = len(alvo)
    if log_fn:
        modo = 'SEM POPUP' if silencioso else 'VISÍVEL'
        log_fn(f'  [API] Disparando detalhar-bolao ({modo}) em {total} cards...')
        if silencioso:
            log_fn('  → Popup OCULTO por CSS — se aparecer algo, é bug.')
            _log_progresso(log_fn, 0, max(total, 1), extra='cliques')

    espera = 0.4 if silencioso else 1.1
    cliques = 0
    for btn in alvo:
        try:
            if silencioso:
                _modo_silencioso(driver, True)  # reforça a cada clique
            driver.execute_script('arguments[0].scrollIntoView({block:"center"});', btn)
            time.sleep(0.08 if silencioso else 0.25)
            try:
                btn.click()
            except Exception:
                driver.execute_script('arguments[0].click();', btn)
            time.sleep(espera)
            # Sempre tenta fechar (mesmo oculto) para não empilhar modais no DOM
            _fechar_popup_rapido(driver)
            cliques += 1
            if silencioso and log_fn and (cliques == total or cliques % 2 == 0 or cliques == 1):
                _log_progresso(log_fn, cliques, total, extra='sem popup')
        except Exception:
            continue

    if silencioso and not manter_oculto:
        _modo_silencioso(driver, False)

    if log_fn:
        if cliques:
            if silencioso:
                _log_progresso(log_fn, cliques, max(total, cliques), extra='ok — aguardando JSON')
            log_fn(f'  [API] {cliques} detalhes disparados — aguardando JSON...')
        else:
            log_fn('  [API] Nenhum botão Detalhes encontrado na página.')
    time.sleep(0.8 if silencioso else 1.5)
    return cliques


def coletar_boloes_das_capturas(
    driver,
    hashes_vistos: Optional[Set[str]] = None,
    log_fn: Optional[Callable[[str], None]] = None,
    filtro_cfg=None,
    parser_slug: str = '',
    filtrar_dezenas: bool = True,
) -> List[Dict[str, Any]]:
    """Lê capturas interceptadas e devolve bolões parseados (deduplicados)."""
    vistos = hashes_vistos if hashes_vistos is not None else set()
    novos: List[Dict[str, Any]] = []

    for cap in ler_capturas_api(driver):
        data = cap.get('data')
        url = cap.get('url', '')
        for bolao in _extrair_boloes_de_captura(data, parser_slug):
            if filtro_cfg:
                try:
                    from boloes_filtro_loterica import bolao_atende_filtro
                    if not bolao_atende_filtro(bolao, filtro_cfg):
                        continue
                except Exception:
                    pass
            h = bolao.get('hash_bolao')
            if not h or not bolao.get('apostas') or h in vistos:
                continue
            vistos.add(h)
            bolao['api_url'] = url
            novos.append(bolao)
            if log_fn:
                lot = (bolao.get('nome_loterica') or '')[:30]
                q1 = bolao.get('qtd_dezenas_aposta_1') or 0
                log_fn(f'  [API] +1 bolão | {lot} | Ap.1={q1} dez.')

    return novos


def aguardar_capturas_api(
    driver,
    minimo: int = 1,
    timeout: float = 15.0,
    intervalo: float = 0.5,
) -> int:
    """Espera até haver pelo menos `minimo` capturas API ou estourar timeout."""
    fim = time.time() + timeout
    while time.time() < fim:
        n = len(ler_capturas_api(driver))
        if n >= minimo:
            return n
        time.sleep(intervalo)
    return len(ler_capturas_api(driver))


def decodificar_path_api(segmento_b64: str) -> str:
    """Decodifica segmento Base64 do path (/rest/v1/{segmento})."""
    import base64
    seg = (segmento_b64 or '').split('?')[0].strip('/')
    for pad in ('', '=', '==', '==='):
        try:
            return base64.b64decode(seg + pad).decode('utf-8', errors='replace')
        except Exception:
            pass
    return seg


def resumo_capturas(driver) -> str:
    """Resumo das URLs capturadas (debug)."""
    linhas = []
    for cap in ler_capturas_api(driver):
        url = cap.get('url', '')
        if API_PREFIX not in url:
            continue
        path = url.split('/rest/v1/')[-1][:80] if '/rest/v1/' in url else url[:80]
        linhas.append(path)
    return '\n'.join(linhas) if linhas else '(nenhuma captura)'


def salvar_capturas_brutas(driver, caminho: str) -> None:
    with open(caminho, 'w', encoding='utf-8') as f:
        json.dump(ler_capturas_api(driver), f, ensure_ascii=False, indent=2)
