# -*- coding: utf-8 -*-
"""
Login automatico no Keycloak da Caixa (loteriasonline.caixa.gov.br).

Importado por _tentar_login_automatico() em baixar_boloes-API.py.
Realiza o fluxo: Sim -> Acessar -> CPF -> codigo e-mail (se presente) -> senha.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, '..'))

CONFIG_PATH = os.path.join(PROJECT_DIR, 'login_caixa_config.json')
CONFIG_EXAMPLE = CONFIG_PATH + '.example'

# Garante que SCRIPT_DIR esteja no sys.path para imports futuros
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)


@dataclass
class CredenciaisCaixa:
    cpf: str = ''
    senha: str = ''


def carregar_config_login() -> CredenciaisCaixa:
    """Le login_caixa_config.json. Retorna CredenciaisCaixa vazio se ausente ou invalido."""
    if not os.path.isfile(CONFIG_PATH):
        return CredenciaisCaixa()
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            data = json.load(f) or {}
        cpf = str(data.get('cpf', '') or '').strip()
        senha = str(data.get('senha', '') or '').strip()
        return CredenciaisCaixa(cpf=cpf, senha=senha)
    except Exception:
        return CredenciaisCaixa()


def salvar_config_login(cpf: str, senha: str) -> None:
    """Grava credenciais no JSON de configuracao."""
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump({'cpf': cpf, 'senha': senha}, f, ensure_ascii=False, indent=2)


def apagar_config_login() -> None:
    """Remove login_caixa_config.json (para usuario poder digitar de novo)."""
    if os.path.isfile(CONFIG_PATH):
        os.remove(CONFIG_PATH)


def _esperar_elemento(driver, by, valor, timeout=12):
    """Espera elemento aparecer. Retorna elemento ou None."""
    try:
        return WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((by, valor))
        )
    except Exception:
        return None


def _clicar_elemento(driver, el) -> bool:
    """Clica elemento (tenta click() e JS click)."""
    if el is None:
        return False
    try:
        el.click()
        return True
    except Exception:
        try:
            driver.execute_script('arguments[0].click();', el)
            return True
        except Exception:
            return False


def _texto_elemento(el) -> str:
    """Retorna texto do elemento (innerText ou text)."""
    if el is None:
        return ''
    try:
        return (el.text or el.get_attribute('innerText') or '').strip()
    except Exception:
        return ''


def executar_login_automatico(driver, *, log_fn: Callable[[str], None], url_boloes: str) -> bool:
    """
    Fluxo completo de login no Keycloak da Caixa (boloes / loteriasonline).

    Passos:
    1. Acessa url_boloes
    2. Clica "Sim" (se presente — tela de boas-vindas)
    3. Clica "Acessar"
    4. Preenche CPF e submete
    5. Clica "Receber codigo no e-mail" (se presente)
    6. Preenche codigo e-mail (se presente)
    7. Preenche senha e submete
    8. Aguarda redirecionamento para loteriasonline.caixa.gov.br

    Retorna True se login com sucesso, False caso contrario.
    """
    wait = WebDriverWait(driver, 12)

    def _log(msg):
        try:
            log_fn(msg)
        except Exception:
            pass

    try:
        # 1. Limpa cookies/eventual sessao e acessa a URL de boloes
        # (forca login completo toda execucao — nunca confia em sessao anterior)
        _log('\n  [LOGIN] Limpando cookies e acessando loteriasonline.caixa.gov.br/bolao-caixa...')
        try:
            driver.delete_all_cookies()
        except Exception:
            pass
        driver.get(url_boloes)
        time.sleep(3.0)

        # Verifica se a sessao esta realmente checando conteudo logado
        # (pode estar com cookies frescos que levam ao Keycloak ainda assim)
        try:
            cur = (driver.execute_script('return window.location.href') or '').lower()
            body = (driver.execute_script('return document.body && document.body.innerText || "";') or '')
            logado_pelo_conteudo = any(w in body.lower() for w in [
                'olá,', 'ola,', 'olá ', 'ola ', 'minha conta', 'sair', 'meu perfil',
            ])
            esta_no_site = 'loteriasonline' in cur and 'login' not in cur and '/auth/' not in cur
            if esta_no_site and logado_pelo_conteudo:
                _log('  [LOGIN] Ja esta logado (conteudo autenticado detectado) — pulando login.')
                return True
            if esta_no_site and not logado_pelo_conteudo:
                _log('  [LOGIN] URL no site mas sem conteudo logado — forçando logout.')
                try:
                    driver.delete_all_cookies()
                except Exception:
                    pass
                driver.get(url_boloes)
                time.sleep(3.0)
        except Exception:
            pass

        # 2. Clica "Sim" (tela de boas-vindas/keycloak) — id="botaosim"
        _log('  [LOGIN] Procurando botao "Sim"...')
        btn_sim = _esperar_elemento(driver, By.ID, 'botaosim', timeout=8)
        if btn_sim is None:
            btn_sim = _esperar_elemento(driver, By.CSS_SELECTOR,
                'button[value="Sim"], button#Sim, button.btn-sim, '
                'button:has-text("Sim") input[type="button"]', timeout=5)
        if btn_sim is None:
            try:
                for btn in driver.find_elements(By.TAG_NAME, 'button'):
                    txt = _texto_elemento(btn)
                    if txt.lower() in ('sim', 'yes', 'continuar', 'aceitar'):
                        btn_sim = btn
                        break
            except Exception:
                pass
        if btn_sim:
            _clicar_elemento(driver, btn_sim)
            _log('  [LOGIN] "Sim" clicado.')
            time.sleep(2.0)

        # 3. Clica "Acessar" / "Entrar" (id="btnLogin") — Keycloak
        _log('  [LOGIN] Procurando botao "Entrar" / "Acessar"...')
        btn_login = _esperar_elemento(driver, By.ID, 'btnLogin', timeout=8)
        if btn_login is None:
            btn_login = _esperar_elemento(driver, By.CSS_SELECTOR,
                'button#Acessar, button.btn-acessar, '
                'a#Acessar, input[value="Acessar"]', timeout=5)
        if btn_login is None:
            try:
                for btn in driver.find_elements(By.CSS_SELECTOR, 'button, a, input[type="button"]'):
                    txt = _texto_elemento(btn)
                    if txt.lower() in ('acessar', 'entrar', 'login', 'sign in'):
                        btn_login = btn
                        break
            except Exception:
                pass
        if not btn_login:
            _log('  [LOGIN] Botao "Entrar/Acessar" nao encontrado — pode ja estar na tela de CPF.')
        else:
            _clicar_elemento(driver, btn_login)
            _log('  [LOGIN] "Entrar/Acessar" clicado.')
            time.sleep(2.5)

        # 4. Preenche CPF — id="username"
        _log('  [LOGIN] Procurando campo de CPF...')
        campo_cpf = _esperar_elemento(driver, By.ID, 'username', timeout=10)
        if campo_cpf is None:
            campo_cpf = _esperar_elemento(driver, By.CSS_SELECTOR,
                'input#cpf, input[name="cpf"], input[placeholder*="CPF"], '
                'input[data-testid*="cpf"], input[aria-label*="CPF"]', timeout=5)
        if campo_cpf is None:
            try:
                for inp in driver.find_elements(By.CSS_SELECTOR, 'input[type="text"], input[type="tel"], input:not([type])'):
                    ph = (inp.get_attribute('placeholder') or '').lower()
                    aid = (inp.get_attribute('aria-label') or '').lower()
                    name = (inp.get_attribute('name') or '').lower()
                    if 'cpf' in ph or 'cpf' in aid or 'cpf' in name or 'cpf' in (inp.get_attribute('id') or ''):
                        campo_cpf = inp
                        break
            except Exception:
                pass
        if not campo_cpf:
            _log('  [LOGIN] ERRO: campo CPF nao encontrado.')
            return False

        campo_cpf.clear()
        campo_cpf.send_keys(Keys.CONTROL + 'a')
        campo_cpf.send_keys(Keys.DELETE)
        time.sleep(0.2)

        # Le credenciais do login_caixa_config.json (evita import circular)
        creds = carregar_config_login()
        cpf_digitado = creds.cpf or None
        senha_digitada = creds.senha or None
        if not cpf_digitado or not senha_digitada:
            _log('  [LOGIN] ERRO: credenciais nao disponiveis.')
            _log('  Verifique login_caixa_config.json (CPF e senha).')
            return False

        campo_cpf.send_keys(cpf_digitado)
        _log(f'  [LOGIN] CPF preenchido: {cpf_digitado[:3]}***')
        time.sleep(0.5)

        # Submete CPF — id="button-submit"
        submit_cpf = _esperar_elemento(driver, By.ID, 'button-submit', timeout=6)
        if submit_cpf is None:
            submit_cpf_list = driver.find_elements(By.CSS_SELECTOR,
                'button[type="submit"], input[type="submit"], '
                'button:has-text("Continuar"), button:has-text("Avancar"), '
                'button:has-text("Enviar")')
            submit_cpf = submit_cpf_list[0] if submit_cpf_list else None
        if submit_cpf:
            _clicar_elemento(driver, submit_cpf)
        else:
            campo_cpf.send_keys(Keys.ENTER)
        _log('  [LOGIN] CPF submetido.')
        time.sleep(2.5)

        # 5. Clica "Receber codigo no e-mail" (id/name="login") — aguarda 20s
        _log('  [LOGIN] Procurando botao "Receber codigo" (name="login")...')
        btn_codigo = _esperar_elemento(driver, By.NAME, 'login', timeout=6)
        if btn_codigo is None:
            btn_codigo = _esperar_elemento(driver, By.CSS_SELECTOR,
                'button:has-text("Receber"), button:has-text("Codigo"), '
                'a:has-text("Codigo"), input[value*="Codigo"]', timeout=4)
        if btn_codigo is None:
            try:
                for btn in driver.find_elements(By.CSS_SELECTOR, 'button, a, span, div[role="button"]'):
                    txt = _texto_elemento(btn).lower()
                    if 'receber' in txt and 'codigo' in txt:
                        btn_codigo = btn
                        break
                    if 'email' in txt and ('codigo' in txt or 'code' in txt):
                        btn_codigo = btn
                        break
            except Exception:
                pass
        if btn_codigo:
            _clicar_elemento(driver, btn_codigo)
            _log('  [LOGIN] "Receber codigo no e-mail" clicado. Aguardando 20s...')
            time.sleep(20.0)

            # 6. Preenche codigo e-mail (se presente)
            campo_codigo = _esperar_elemento(driver, By.CSS_SELECTOR,
                'input[name="codigo"], input[name="code"], input[placeholder*="Codigo"], '
                'input[placeholder*="codigo"], input[aria-label*="Codigo"]', timeout=8)
            if campo_codigo:
                _log('  [LOGIN] Campo de codigo encontrado — aguardando preenchimento manual.')
                _log('  (Ou pressione ENTER para pular se ja preenchido)')
                try:
                    WebDriverWait(driver, 60).until(
                        lambda d: (campo_codigo.get_attribute('value') or '').strip() != ''
                    )
                    _log('  [LOGIN] Codigo preenchido.')
                    time.sleep(0.3)
                    submit_codigo = driver.find_elements(By.CSS_SELECTOR,
                        'button[type="submit"], button:has-text("Continuar"), button:has-text("Avancar")')
                    if submit_codigo:
                        _clicar_elemento(driver, submit_codigo[0])
                    else:
                        campo_codigo.send_keys(Keys.ENTER)
                    time.sleep(2.0)
                except Exception:
                    _log('  [LOGIN] Timeout aguardando codigo e-mail — continuando.')
            else:
                _log('  [LOGIN] Campo de codigo nao encontrado — continuando.')
        else:
            _log('  [LOGIN] Botao "Receber codigo e-mail" nao encontrado — pulando passo.')

        # 7. Preenche senha — id="password"
        _log('  [LOGIN] Procurando campo de senha...')
        campo_senha = _esperar_elemento(driver, By.ID, 'password', timeout=10)
        if campo_senha is None:
            campo_senha = _esperar_elemento(driver, By.CSS_SELECTOR,
                'input#password, input[name="password"], input[type="password"], '
                'input[placeholder*="Senha"], input[placeholder*="senha"], '
                'input[aria-label*="Senha"], input[aria-label*="senha"]', timeout=5)
        if campo_senha is None:
            try:
                for inp in driver.find_elements(By.CSS_SELECTOR, 'input[type="password"]'):
                    campo_senha = inp
                    break
            except Exception:
                pass
        if not campo_senha:
            _log('  [LOGIN] ERRO: campo de senha nao encontrado.')
            return False

        campo_senha.clear()
        campo_senha.clear()
        campo_senha.send_keys(Keys.CONTROL + 'a')
        campo_senha.send_keys(Keys.DELETE)
        time.sleep(0.2)
        if not senha_digitada:
            _log('  [LOGIN] ERRO: senha nao disponivel.')
            return False
        campo_senha.send_keys(senha_digitada)
        _log('  [LOGIN] Senha preenchida: ***')
        time.sleep(0.3)

        # Submete senha — tabindex="1" (botao Entrar)
        submit_senha = _esperar_elemento(driver, By.CSS_SELECTOR,
            'input[tabindex="1"], button[tabindex="1"], a[tabindex="1"]', timeout=6)
        if submit_senha is None:
            submit_senha_list = driver.find_elements(By.CSS_SELECTOR,
                'button[type="submit"], input[type="submit"], '
                'button:has-text("Entrar"), button:has-text("Acessar"), '
                'button:has-text("Login")')
            submit_senha = submit_senha_list[0] if submit_senha_list else None
        if submit_senha:
            _clicar_elemento(driver, submit_senha)
        else:
            campo_senha.send_keys(Keys.ENTER)
        _log('  [LOGIN] Senha submetida — navegador deve redirecionar.')
        time.sleep(8.0)

        # 8. Verifica se esta na pagina de boloes
        try:
            cur = (driver.execute_script('return window.location.href') or '').lower()
            if 'loteriasonline' in cur and ('bolao' in cur or 'silce-web' in cur):
                _log('  [LOGIN] Redirecionado para paginas de boloes — login OK!')
                return True
            if 'login.caixa.gov.br' in cur or '/auth/' in cur or 'openid-connect' in cur:
                _log('  [LOGIN] ERRO: ainda na tela de login — credenciais ou fluxo invalido.')
                return False
        except Exception:
            pass

        # Fallback: verifica texto da pagina
        try:
            body = (driver.execute_script('return document.body && document.body.innerText || "";') or '')
            if any(w in body.lower() for w in ['ol�', 'ola', 'minha conta', 'sair']):
                _log('  [LOGIN] Sessao ativa detectada pelo texto da pagina.')
                return True
        except Exception:
            pass

        _log('  [LOGIN] Resultado inconclusivo — verifique o navegador.')
        return False

    except Exception as exc:
        try:
            log_fn(f'\n  [LOGIN] ERRO: {exc}')
        except Exception:
            pass
        traceback.print_exc()
        return False
