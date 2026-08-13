# -*- coding: utf-8 -*-
"""
Cards em #/inicio-bolao — lê modalidade + concurso e abre a lista via
"Compre seu bolão" (ciclo automático [C]).
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, List, Optional

from boloes_modalidades import TODAS_MODALIDADES, resolver_modalidade_menu

URL_INICIO_BOLAO = (
    'https://www.loteriasonline.caixa.gov.br/silce-web/#/inicio-bolao'
)

LogFn = Callable[[str], None]

# Sobe no DOM até achar bloco com "concurso NNN" (o card real, não só o botão).
_JS_LISTAR_CARDS = r"""
var out = [];
function visivel(el) {
  if (!el) return false;
  try {
    var st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
  } catch (e) {}
  return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
}
function textoEl(el) {
  return ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
}
function cardComConcurso(btn) {
  var el = btn;
  var melhor = null;
  for (var i = 0; i < 18 && el; i++) {
    var t = textoEl(el);
    if (t && /concurso\s*\d{2,6}/i.test(t) && t.length >= 25 && t.length < 2500) {
      melhor = el;
      // Continua um pouco — às vezes o card maior tem o nome da modalidade
      if (/mega|quina|lotof|timemania|dia de sorte|super sete|dupla|milion|lotomania/i.test(t)) {
        return el;
      }
    }
    el = el.parentElement;
  }
  return melhor;
}
function ehBotaoCompre(b) {
  var lab = textoEl(b).toLowerCase();
  if (lab.indexOf('compre') >= 0 && lab.indexOf('bol') >= 0) return true;
  var cls = ((b.className || '') + '').toLowerCase();
  if (cls.indexOf('compre-seu-bolao') >= 0) return true;
  return false;
}
var btns = Array.prototype.slice.call(document.querySelectorAll('button, a, [role="button"]'));
var vistos = {};
for (var i = 0; i < btns.length; i++) {
  var b = btns[i];
  if (!visivel(b) || !ehBotaoCompre(b)) continue;
  var card = cardComConcurso(b);
  var texto = textoEl(card);
  if (!texto || !/concurso\s*\d{2,6}/i.test(texto)) continue;
  var m = texto.match(/concurso\s*(\d{2,6})/i);
  var conc = m ? m[1] : '';
  var key = (conc || '') + '|' + texto.slice(0, 60).toLowerCase();
  if (vistos[key]) continue;
  vistos[key] = true;
  out.push({ texto: texto.slice(0, 500), concurso: conc, indice: out.length });
}
// Fallback: blocos com "concurso N" mesmo sem botão classificado
if (out.length === 0) {
  var all = document.querySelectorAll('div, section, article, li, mat-card');
  for (var j = 0; j < all.length; j++) {
    var el2 = all[j];
    if (!visivel(el2)) continue;
    var t2 = textoEl(el2);
    if (!t2 || t2.length < 30 || t2.length > 800) continue;
    if (!/concurso\s*\d{2,6}/i.test(t2)) continue;
    if (t2.toLowerCase().indexOf('compre') < 0) continue;
    var m2 = t2.match(/concurso\s*(\d{2,6})/i);
    var c2 = m2 ? m2[1] : '';
    var k2 = c2 + '|' + t2.slice(0, 60).toLowerCase();
    if (vistos[k2]) continue;
    vistos[k2] = true;
    out.push({ texto: t2.slice(0, 500), concurso: c2, indice: out.length });
  }
}
return out;
"""

_JS_CLICAR_CARD = r"""
var alvoTexto = arguments[0] || '';
var alvoConc = arguments[1] || '';
function visivel(el) {
  if (!el) return false;
  try {
    var st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
  } catch (e) {}
  return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
}
function textoEl(el) {
  return ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
}
function cardComConcurso(btn) {
  var el = btn;
  var melhor = null;
  for (var i = 0; i < 18 && el; i++) {
    var t = textoEl(el);
    if (t && /concurso\s*\d{2,6}/i.test(t) && t.length >= 25 && t.length < 2500) {
      melhor = el;
      if (/mega|quina|lotof|timemania|dia de sorte|super sete|dupla|milion|lotomania/i.test(t)) {
        return el;
      }
    }
    el = el.parentElement;
  }
  return melhor;
}
function norm(s) {
  return (s || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
}
function ehBotaoCompre(b) {
  var lab = textoEl(b).toLowerCase();
  if (lab.indexOf('compre') >= 0 && lab.indexOf('bol') >= 0) return true;
  var cls = ((b.className || '') + '').toLowerCase();
  return cls.indexOf('compre-seu-bolao') >= 0;
}
var alvoN = norm(alvoTexto);
var btns = Array.prototype.slice.call(document.querySelectorAll('button, a, [role="button"]'));
for (var i = 0; i < btns.length; i++) {
  var b = btns[i];
  if (!visivel(b) || !ehBotaoCompre(b)) continue;
  var card = cardComConcurso(b);
  var texto = textoEl(card) || textoEl(b);
  var tn = norm(texto);
  var okNome = !alvoN;
  if (alvoN) {
    if (tn.indexOf(alvoN) >= 0) okNome = true;
    else {
      var partes = alvoN.split(/\s+/).filter(function(p){ return p.length > 3; });
      if (partes.length && partes.every(function(p){ return tn.indexOf(p) >= 0; })) okNome = true;
      // mega-sena / dia-de-sorte etc.
      var slug = alvoN.replace(/[^a-z0-9]+/g, '');
      if (slug && tn.replace(/[^a-z0-9]+/g, '').indexOf(slug) >= 0) okNome = true;
    }
  }
  var okConc = true;
  if (alvoConc) {
    var m = texto.match(/concurso\s*(\d{2,6})/i);
    okConc = !!(m && m[1] === String(alvoConc));
  }
  if (okNome && okConc) {
    try { b.scrollIntoView({block: 'center'}); } catch (e) {}
    b.click();
    return true;
  }
}
// Só concurso
if (alvoConc) {
  for (var j = 0; j < btns.length; j++) {
    var b2 = btns[j];
    if (!visivel(b2) || !ehBotaoCompre(b2)) continue;
    var t2 = textoEl(cardComConcurso(b2));
    var m2 = t2.match(/concurso\s*(\d{2,6})/i);
    if (m2 && m2[1] === String(alvoConc)) {
      try { b2.scrollIntoView({block: 'center'}); } catch (e) {}
      b2.click();
      return true;
    }
  }
}
return false;
"""

_JS_DEBUG_PAGINA = r"""
return {
  url: location.href || '',
  title: document.title || '',
  nCompre: (document.body.innerText || '').toLowerCase().split('compre seu').length - 1,
  nConcurso: (document.body.innerText || '').toLowerCase().split('concurso').length - 1,
  sample: ((document.body.innerText || '').replace(/\s+/g, ' ').trim()).slice(0, 300)
};
"""


def ir_para_inicio_bolao(driver, log_fn: LogFn = print, espera: float = 3.5) -> bool:
    if driver is None:
        return False
    try:
        # Hash SPA: às vezes precisa forçar reload do hash
        cur = ''
        try:
            cur = driver.current_url or ''
        except Exception:
            pass
        if 'inicio-bolao' not in cur:
            driver.get(URL_INICIO_BOLAO)
        else:
            driver.get(URL_INICIO_BOLAO)
        time.sleep(espera)
        # Dispara hashchange se a URL não mudou o conteúdo
        try:
            driver.execute_script(
                "if (location.hash !== '#/inicio-bolao') {"
                "  location.hash = '#/inicio-bolao';"
                "}"
            )
            time.sleep(1.5)
        except Exception:
            pass
        return True
    except Exception as exc:
        log_fn(f'  [CARDS] Falha ao abrir inicio-bolao: {exc}')
        return False


def _melhor_modalidade_do_texto(texto: str):
    """Prefere especial / label mais longo (ex.: Lotofácil da Independência > Lotofácil)."""
    texto = (texto or '').strip()
    if not texto:
        return None
    # Remove o botão do texto para não confundir
    texto_limpo = re.sub(r'compre\s+seu\s+bol[aã]o', ' ', texto, flags=re.I)
    linhas = [ln.strip() for ln in re.split(r'[\n\r]+', texto_limpo) if ln.strip()]
    candidatos = []
    for pedaco in ([texto_limpo] + linhas[:6]):
        mod = resolver_modalidade_menu(pedaco)
        if mod:
            score = len(mod.label) + (50 if mod.especial else 0)
            candidatos.append((score, mod))
    if not candidatos:
        low = texto_limpo.lower()
        for mod in TODAS_MODALIDADES:
            for alvo in (mod.label,) + tuple(mod.keywords) + (mod.slug.replace('-', ' '),):
                if alvo and str(alvo).lower() in low:
                    score = len(str(alvo)) + (50 if mod.especial else 0)
                    candidatos.append((score, mod))
                    break
    if not candidatos:
        return None
    candidatos.sort(key=lambda x: x[0], reverse=True)
    return candidatos[0][1]


def _brutos_com_retry(driver, log_fn: LogFn, tentativas: int = 8, intervalo: float = 1.5) -> List[dict]:
    ultimo: List[dict] = []
    for n in range(1, tentativas + 1):
        try:
            ultimo = driver.execute_script(_JS_LISTAR_CARDS) or []
        except Exception as exc:
            log_fn(f'  [CARDS] Erro JS (tentativa {n}): {exc}')
            ultimo = []
        # Só conta cards que tenham "concurso"
        bons = [
            x for x in ultimo
            if isinstance(x, dict) and re.search(r'concurso\s*\d{2,6}', str(x.get('texto') or ''), re.I)
        ]
        if bons:
            return bons
        if n == 1 or n == tentativas:
            try:
                dbg = driver.execute_script(_JS_DEBUG_PAGINA) or {}
                log_fn(
                    f'  [CARDS] Aguardando cards… try {n}/{tentativas} | '
                    f'url={dbg.get("url", "?")} | compre≈{dbg.get("nCompre")} | '
                    f'concurso≈{dbg.get("nConcurso")}'
                )
                if n == tentativas and dbg.get('sample'):
                    log_fn(f'  [CARDS] Amostra página: {str(dbg.get("sample"))[:180]!r}')
            except Exception:
                pass
        time.sleep(intervalo)
    return ultimo if isinstance(ultimo, list) else []


def listar_cards_modalidades(driver, log_fn: LogFn = print) -> List[Dict[str, Any]]:
    """
    Retorna cards reconhecidos: {slug, label, concurso, texto, especial}.
    Um por modalidade (slug único).
    """
    if driver is None:
        return []

    brutos = _brutos_com_retry(driver, log_fn)

    por_slug: Dict[str, Dict[str, Any]] = {}
    for item in brutos:
        if not isinstance(item, dict):
            continue
        texto = str(item.get('texto') or '')
        if texto.strip().lower() in ('compre seu bolão', 'compre seu bolao'):
            continue
        conc = re.sub(r'\D', '', str(item.get('concurso') or ''))
        if not conc:
            m = re.search(r'concurso\s*(\d{2,6})', texto, re.I)
            if m:
                conc = m.group(1)
        mod = _melhor_modalidade_do_texto(texto)
        if not mod:
            log_fn(f'  [CARDS] Ignorado (modalidade desconhecida): {texto[:100]!r}')
            continue
        prev = por_slug.get(mod.slug)
        if prev and prev.get('especial') and not mod.especial:
            continue
        # Preferir card com concurso preenchido
        if prev and prev.get('concurso') and not conc:
            continue
        por_slug[mod.slug] = {
            'slug': mod.slug,
            'label': mod.label,
            'parser_slug': mod.parser_slug,
            'concurso': conc,
            'texto': texto[:200],
            'especial': bool(mod.especial),
            'mod': mod,
        }

    cards = list(por_slug.values())

    def _ordem(c: dict) -> tuple:
        slug = c['slug']
        if slug == 'dia-de-sorte':
            return (0, 0, slug)
        if c.get('especial'):
            return (1, int(getattr(c['mod'], 'numero', 99) or 99), slug)
        return (2, int(getattr(c['mod'], 'numero', 99) or 99), slug)

    cards.sort(key=_ordem)
    log_fn(f'  [CARDS] {len(cards)} modalidade(s) detectada(s) em inicio-bolao.')
    for c in cards:
        log_fn(f'    • {c["label"]:<28} concurso {c["concurso"] or "?"}')
    return cards


def abrir_lista_do_card(
    driver,
    *,
    label: str,
    concurso: str = '',
    slug: str = '',
    log_fn: LogFn = print,
    timeout_detalhes: float = 25.0,
) -> bool:
    """
    Garante inicio-bolao, clica Compre seu bolão do card e espera botões Detalhes.
    """
    if driver is None:
        return False
    if not ir_para_inicio_bolao(driver, log_fn=log_fn):
        return False
    # Garante que os cards carregaram antes do clique
    _brutos_com_retry(driver, log_fn, tentativas=6, intervalo=1.2)

    busca = (label or slug or '').strip()
    aliases = [busca]
    if slug:
        aliases.append(slug.replace('-', ' '))
    if 'independ' in busca.lower() or (slug and 'independ' in slug):
        aliases.extend(['lotofacil da independencia', 'independencia', 'lotofácil da independência'])
    if 'milion' in busca.lower() or (slug and 'milion' in slug):
        aliases.extend(['+milionária', 'milionaria', 'mais milionaria', '+milionaria'])

    clicou = False
    for alias in aliases:
        try:
            ok = driver.execute_script(_JS_CLICAR_CARD, alias, str(concurso or ''))
            if ok:
                clicou = True
                break
        except Exception:
            continue
    if not clicou and concurso:
        try:
            clicou = bool(driver.execute_script(_JS_CLICAR_CARD, '', str(concurso)))
        except Exception:
            clicou = False
    if not clicou:
        log_fn(f'  [CARDS] Não clicou Compre seu bolão: {label} (conc {concurso}).')
        return False

    log_fn(f'  [CARDS] Abriu lista: {label} | concurso {concurso or "?"}')
    time.sleep(2.0)
    try:
        from boloes_api_caixa import aguardar_detalhes_visiveis
        n = aguardar_detalhes_visiveis(
            driver, minimo=1, timeout=int(timeout_detalhes), log_fn=log_fn,
        )
        if n < 1:
            log_fn('  [CARDS] Lista abriu, mas 0 Detalhes ainda — aguardando mais 8s...')
            time.sleep(8)
            n = aguardar_detalhes_visiveis(driver, minimo=1, timeout=12, log_fn=log_fn)
        return n >= 1
    except Exception as exc:
        log_fn(f'  [CARDS] Aviso Detalhes: {exc}')
        return True
