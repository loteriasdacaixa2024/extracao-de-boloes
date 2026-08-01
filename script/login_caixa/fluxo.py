# -*- coding: utf-8 -*-
"""
Fluxo de login automatizado (etapas 1–9) até Entrar.
Após Entrar, a automação encerra — navegação restante é manual.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from selenium.common.exceptions import InvalidElementStateException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from . import seletores as S
from .credenciais import CredenciaisCaixa, CredenciaisError, carregar_credenciais
from .driver_edge import criar_driver_edge
from .logger_login import configurar_logger
from .mascara import mascarar_cpf
from .waits import (
    ElementoNaoEncontrado,
    aguardar_campo_preenchido_sem_ler_valor,
    aguardar_elemento_aparecer_apos_acao_manual,
    espera_minima_seguranca,
    esperar_clicavel,
    esperar_id_clicavel,
    esperar_id_presente,
)


class LoginAutomatizadoError(RuntimeError):
    """Falha controlada no fluxo de login."""


def _clicar_id(driver: WebDriver, element_id: str, logger: logging.Logger, etapa: str) -> None:
    logger.info("%s — aguardando botão id=%s clicável", etapa, element_id)
    el = esperar_id_clicavel(driver, element_id, descricao=f"{etapa} id={element_id}")
    try:
        el.click()
    except Exception as exc:
        logger.warning("%s — click() falhou (%s); tentando JS click", etapa, type(exc).__name__)
        driver.execute_script("arguments[0].click();", el)
    logger.info("%s — clique realizado em id=%s", etapa, element_id)
    espera_minima_seguranca()


def _clicar_candidatos(
    driver: WebDriver,
    candidatos: tuple,
    logger: logging.Logger,
    etapa: str,
    *,
    timeout: float | None = None,
) -> None:
    """Clica no primeiro seletor candidado que ficar clicável."""
    limite = time.time() + (timeout or S.TIMEOUT_PADRAO)
    ultimo = ""
    while time.time() < limite:
        for tipo, seletor in candidatos:
            try:
                by = _by_de(tipo)
                for el in driver.find_elements(by, seletor):
                    try:
                        if not el.is_displayed() or not el.is_enabled():
                            continue
                        logger.info("%s — clicando via %s=%s", etapa, tipo, seletor)
                        try:
                            el.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", el)
                        logger.info("%s — clique realizado", etapa)
                        espera_minima_seguranca()
                        return
                    except WebDriverException as exc:
                        ultimo = str(exc)
                        continue
            except WebDriverException as exc:
                ultimo = str(exc)
                continue
        time.sleep(S.POLL_INTERVALO)
    raise ElementoNaoEncontrado(
        f"{etapa}: nenhum botão candidado ficou clicável. Último erro: {ultimo or 'n/a'}"
    )


def _by_de(tipo: str) -> str:
    t = (tipo or "").lower()
    if t == "id":
        return By.ID
    if t == "css":
        return By.CSS_SELECTOR
    if t == "xpath":
        return By.XPATH
    raise ValueError(f"Tipo de seletor inválido: {tipo}")


def _entrar_contexto_login(driver: WebDriver, logger: logging.Logger) -> None:
    """
    Após Acessar, o CPF pode abrir em nova aba/janela ou iframe (login.caixa.gov.br).
    """
    fim = time.time() + S.TIMEOUT_POS_ACESSAR
    handle_origem = driver.current_window_handle

    while time.time() < fim:
        for handle in driver.window_handles:
            try:
                driver.switch_to.window(handle)
                url = (driver.current_url or "").lower()
                if "login.caixa" in url or "openid" in url or "auth/realms" in url:
                    logger.info("Contexto login: janela/aba URL=%s", url.split("?")[0])
                    return
            except WebDriverException:
                continue

        try:
            driver.switch_to.window(handle_origem)
        except WebDriverException:
            pass
        driver.switch_to.default_content()
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for idx, frame in enumerate(iframes):
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(frame)
                if driver.find_elements(By.ID, S.ID_CAMPO_CPF) or driver.find_elements(
                    By.CSS_SELECTOR, "input#template, input[name='username'], input[name='cpf']"
                ):
                    logger.info("Contexto login: iframe índice=%s", idx)
                    return
            except WebDriverException:
                continue

        driver.switch_to.default_content()
        if driver.find_elements(By.ID, S.ID_CAMPO_CPF):
            logger.info("Contexto login: documento principal")
            return

        time.sleep(S.POLL_INTERVALO)

    driver.switch_to.default_content()
    logger.warning(
        "Não confirmou janela/iframe de login a tempo — seguindo no contexto atual."
    )


def _localizar_campo_cpf(driver: WebDriver, logger: logging.Logger) -> WebElement:
    """Localiza o INPUT de CPF (nunca div#template — esse id é só o container)."""
    fim = time.time() + S.TIMEOUT_POS_ACESSAR
    ultimo_erro = ""

    while time.time() < fim:
        for tipo, seletor in S.CAMPOS_CPF_CANDIDATOS:
            try:
                by = _by_de(tipo)
                candidatos = driver.find_elements(by, seletor)
            except WebDriverException as exc:
                ultimo_erro = str(exc)
                continue
            for el in candidatos:
                try:
                    tag = (el.tag_name or "").lower()
                    # Obrigatório: só <input> — div#template não recebe digitação
                    if tag != "input":
                        continue
                    if not el.is_displayed():
                        continue
                    tipo_input = (el.get_attribute("type") or "text").lower()
                    if tipo_input in ("hidden", "submit", "button", "checkbox", "radio", "password"):
                        continue
                    logger.info(
                        "Campo CPF encontrado via %s=%s (tag=%s type=%s)",
                        tipo,
                        seletor,
                        tag,
                        tipo_input,
                    )
                    return el
                except WebDriverException:
                    continue
        time.sleep(S.POLL_INTERVALO)

    raise ElementoNaoEncontrado(
        "INPUT de CPF não encontrado após Acessar "
        f"(timeout {S.TIMEOUT_POS_ACESSAR}s). "
        "Obs: id=template é um div container — buscamos input dentro dele. "
        f"Último erro: {ultimo_erro or 'n/a'}"
    )


def _valor_cpf_no_campo(el: WebElement) -> str:
    try:
        return (el.get_attribute("value") or "").replace(".", "").replace("-", "").strip()
    except Exception:
        return ""


def _preencher_cpf(
    driver: WebDriver,
    valor: str,
    logger: logging.Logger,
) -> None:
    """
    Digita no INPUT real id=username (Keycloak Caixa).
    Digitação caractere a caractere + setter nativo do HTMLInputElement.
    """
    from selenium.webdriver.common.action_chains import ActionChains

    logger.info("Etapa 4 — Informar CPF — localizando input#username")
    el = _localizar_campo_cpf(driver, logger)
    el_id = el.get_attribute("id") or "?"
    logger.info("Etapa 4 — Usando elemento id=%s name=%s", el_id, el.get_attribute("name") or "?")

    try:
        WebDriverWait(driver, 15).until(EC.element_to_be_clickable(el))
    except Exception:
        pass

    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    try:
        el.click()
    except Exception:
        driver.execute_script("arguments[0].click();", el)
    time.sleep(0.3)

    # Limpa sem clear()
    try:
        el.send_keys(Keys.CONTROL, "a")
        el.send_keys(Keys.BACKSPACE)
    except Exception:
        driver.execute_script(
            """
            const el = arguments[0];
            const setter = Object.getOwnPropertyDescriptor(
              window.HTMLInputElement.prototype, 'value'
            ).set;
            setter.call(el, '');
            el.dispatchEvent(new Event('input', { bubbles: true }));
            """,
            el,
        )

    # Digitação humana (melhor contra máscara / Topaz)
    digitou = False
    try:
        actions = ActionChains(driver)
        actions.move_to_element(el).click()
        for digito in valor:
            actions.send_keys(digito)
            actions.pause(0.05)
        actions.perform()
        digitou = True
        logger.info("Etapa 4 — Digitação caractere a caractere concluída")
    except Exception as exc:
        logger.warning("ActionChains falhou (%s); tentando send_keys", type(exc).__name__)
        try:
            el.send_keys(valor)
            digitou = True
        except (InvalidElementStateException, WebDriverException) as exc2:
            logger.warning("send_keys falhou (%s)", type(exc2).__name__)

    atual = _valor_cpf_no_campo(el)
    if len(atual) < 11:
        # Setter nativo do input (funciona onde el.value= direto é ignorado)
        driver.execute_script(
            """
            const el = arguments[0];
            const v = arguments[1];
            el.focus();
            const proto = window.HTMLInputElement.prototype;
            const desc = Object.getOwnPropertyDescriptor(proto, 'value');
            if (desc && desc.set) { desc.set.call(el, v); }
            else { el.value = v; }
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
            """,
            el,
            valor,
        )
        logger.info("Etapa 4 — CPF aplicado via HTMLInputElement value setter")
        atual = _valor_cpf_no_campo(el)

    if len(atual) < 11:
        raise LoginAutomatizadoError(
            "CPF não ficou no input#username (campo ainda vazio após digitação). "
            f"id usado={el_id}. Confira o Inspector."
        )

    logger.info("Etapa 4 — Campo preenchido (CPF informado: %s)", mascarar_cpf(valor))
    espera_minima_seguranca()


def executar_etapas(
    driver: WebDriver,
    credenciais: CredenciaisCaixa,
    logger: logging.Logger,
) -> None:
    """Executa o fluxo completo no driver já aberto. Não fecha o navegador."""

    logger.info("Etapa 1 — Abrindo portal (termos de uso)")
    driver.get(S.URL_TERMOS)
    esperar_id_presente(
        driver,
        S.ID_BOTAO_SIM,
        timeout=S.TIMEOUT_PADRAO,
        descricao="Etapa 1 — página termos (botaosim)",
    )
    logger.info("Etapa 1 — Página carregada")

    _clicar_id(driver, S.ID_BOTAO_SIM, logger, "Etapa 2 — Clique Sim (+18)")

    logger.info(
        "Etapa 3 — Aguardando botão Acessar id=%s (após fechar termos)",
        S.ID_BOTAO_ACESSAR,
    )
    _clicar_id(driver, S.ID_BOTAO_ACESSAR, logger, "Etapa 3 — Clique Acessar")

    _entrar_contexto_login(driver, logger)
    _preencher_cpf(driver, credenciais.cpf, logger)

    _clicar_id(driver, S.ID_BOTAO_ENVIAR_CPF, logger, "Etapa 5 — Confirmar CPF / Próximo")

    # Etapa 6 — "Receber código" (name=login, não id)
    logger.info("Etapa 6 — Clicar em Receber código (name=login)")
    _clicar_candidatos(
        driver,
        S.BOTAO_RECEBER_CODIGO_CANDIDATOS,
        logger,
        "Etapa 6 — Receber código",
    )

    logger.info(
        "Etapa 7 — Aguardando campo id=%s. Digite o código do e-mail no navegador. "
        "NÃO clique automático em Enviar — faça isso manualmente.",
        S.ID_CAMPO_CODIGO,
    )
    esperar_id_presente(
        driver,
        S.ID_CAMPO_CODIGO,
        timeout=S.TIMEOUT_PADRAO,
        descricao="Etapa 7 — campo código",
    )
    logger.info(
        "Etapa 7 — Campo código visível. Aguardando preenchimento pelo operador "
        "(timeout %ss). O valor NÃO será lido nem gravado em log.",
        S.TIMEOUT_CODIGO_MANUAL,
    )

    aguardar_campo_preenchido_sem_ler_valor(
        driver,
        S.ID_CAMPO_CODIGO,
        timeout=S.TIMEOUT_CODIGO_MANUAL,
        min_chars=1,
    )
    logger.info(
        "Etapa 7 — Código detectado no campo (conteúdo omitido). "
        "Aguardando operador clicar em Enviar (id=%s). Automação NÃO clica.",
        S.ID_BOTAO_LOGIN,
    )

    aguardar_elemento_aparecer_apos_acao_manual(
        driver,
        S.ID_CAMPO_SENHA,
        timeout=S.TIMEOUT_CODIGO_MANUAL,
        descricao="Etapa 7/8 — campo senha após Enviar manual",
    )
    logger.info("Etapa 7 — Confirmação manual do código concluída (campo senha apareceu)")

    logger.info("Etapa 8 — Senha carregada com sucesso (valor omitido no log)")
    el_senha = esperar_id_presente(
        driver,
        S.ID_CAMPO_SENHA,
        descricao="Etapa 8 — campo password",
    )
    try:
        el_senha.click()
        el_senha.send_keys(Keys.CONTROL, "a")
        el_senha.send_keys(Keys.BACKSPACE)
        el_senha.send_keys(credenciais.senha)
    except Exception:
        driver.execute_script(
            """
            const el = arguments[0], v = arguments[1];
            el.focus(); el.value = v;
            el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
            """,
            el_senha,
            credenciais.senha,
        )
    logger.info("Etapa 8 — Campo senha preenchido")
    espera_minima_seguranca()

    logger.info("Etapa 9 — Aguardando botão Entrar (%s)", S.CSS_BOTAO_ENTRAR)
    try:
        btn = esperar_clicavel(
            driver,
            By.CSS_SELECTOR,
            S.CSS_BOTAO_ENTRAR,
            timeout=15,
            descricao="Etapa 9 — button[tabindex='1']",
        )
        try:
            btn.click()
        except Exception:
            driver.execute_script("arguments[0].click();", btn)
    except ElementoNaoEncontrado:
        _clicar_candidatos(
            driver,
            S.CSS_BOTAO_ENTRAR_ALT,
            logger,
            "Etapa 9 — Entrar (alternativos)",
        )
    logger.info("Etapa 9 — Clique em Entrar realizado")

    # CRÍTICO: esperar o OAuth voltar ao silce-web ANTES de qualquer driver.get()
    # Se navegar cedo demais, a sessão cai e o site pede CPF de novo.
    _aguardar_retorno_portal_logado(driver, logger)
    logger.info(
        "Automação de login ENCERRADA com sessão no portal. "
        "Não recarregue a página de login."
    )


def _parece_logado_no_portal(driver: WebDriver) -> bool:
    """True se estiver no silce e o botão Acessar (btnLogin) não estiver visível."""
    try:
        url = (driver.current_url or "").lower()
        if S.HOST_PORTAL not in url:
            return False
        if "login.caixa" in url:
            return False
        # btnLogin só existe quando deslogado (ng-if=!usuarioLogado)
        for el in driver.find_elements(By.ID, S.ID_BOTAO_ACESSAR):
            try:
                if el.is_displayed():
                    return False
            except WebDriverException:
                continue
        # Sinais positivos
        try:
            body = (driver.find_element(By.TAG_NAME, "body").text or "")
            if any(x in body for x in ("Olá", "Ola", "Sair", "Minha conta", "Minha Conta")):
                return True
        except WebDriverException:
            pass
        # No portal sem btnLogin visível → tratado como logado
        return "silce-web" in url
    except WebDriverException:
        return False


def _aguardar_retorno_portal_logado(
    driver: WebDriver,
    logger: logging.Logger,
    *,
    timeout: float | None = None,
) -> None:
    """
    Após Entrar, o Keycloak redireciona de volta ao loteriasonline.
    Mantém o foco nessa janela e NÃO navega manualmente até a sessão estabilizar.
    """
    limite = timeout or S.TIMEOUT_RETORNO_OAUTH
    logger.info(
        "Aguardando retorno OAuth ao portal (%ss) — não interromper o redirect...",
        int(limite),
    )
    fim = time.time() + limite
    while time.time() < fim:
        for handle in list(driver.window_handles):
            try:
                driver.switch_to.window(handle)
                url = (driver.current_url or "").lower()
                if S.HOST_PORTAL in url and "login.caixa" not in url:
                    if _parece_logado_no_portal(driver):
                        # pequena estabilização de cookies/token
                        time.sleep(2)
                        if _parece_logado_no_portal(driver):
                            logger.info(
                                "Sessão ativa no portal: %s",
                                (driver.current_url or "").split("?")[0],
                            )
                            return
            except WebDriverException:
                continue
        time.sleep(S.POLL_INTERVALO)

    # Fallback: foca qualquer aba do portal mesmo sem confirmação forte
    for handle in list(driver.window_handles):
        try:
            driver.switch_to.window(handle)
            url = (driver.current_url or "").lower()
            if S.HOST_PORTAL in url and "login.caixa" not in url:
                logger.warning(
                    "Timeout parcial — permanecendo no portal: %s "
                    "(confirme se aparece logado no Edge).",
                    url.split("?")[0],
                )
                return
        except WebDriverException:
            continue

    logger.warning(
        "Não detectou retorno ao portal a tempo. "
        "Se o Edge pedir CPF de novo, o redirect OAuth foi interrompido."
    )


def executar_login_automatizado(
    *,
    manter_navegador_aberto: bool = True,
    driver: Optional[WebDriver] = None,
    credenciais: Optional[CredenciaisCaixa] = None,
) -> WebDriver:
    """Ponto de entrada público. Retorna o WebDriver com a sessão ativa."""
    logger = configurar_logger()
    logger.info("=== Início login automatizado Caixa (módulo isolado) ===")

    proprio_driver = driver is None
    drv = driver

    try:
        creds = credenciais or carregar_credenciais()
        logger.info("Credenciais carregadas. CPF: %s", mascarar_cpf(creds.cpf))
        logger.info("Senha carregada com sucesso.")

        if drv is None:
            drv = criar_driver_edge(logger)

        executar_etapas(drv, creds, logger)
        return drv

    except CredenciaisError as exc:
        logger.error("Interrompido (credenciais): %s", exc)
        raise LoginAutomatizadoError(str(exc)) from exc
    except ElementoNaoEncontrado as exc:
        logger.error("Interrompido (elemento): %s", exc)
        raise LoginAutomatizadoError(str(exc)) from exc
    except LoginAutomatizadoError:
        raise
    except Exception as exc:
        logger.exception("Interrompido (erro inesperado): %s", exc)
        raise LoginAutomatizadoError(f"Erro inesperado no login: {exc}") from exc
    finally:
        if not manter_navegador_aberto and proprio_driver and drv is not None:
            try:
                drv.quit()
                logger.info("Navegador fechado (manter_navegador_aberto=False).")
            except Exception:
                pass
