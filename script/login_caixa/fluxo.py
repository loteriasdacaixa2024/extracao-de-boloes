# -*- coding: utf-8 -*-
"""
Fluxo de login automatizado (etapas 1–9) até Entrar.
Após Entrar, a automação encerra — navegação restante é manual.
"""

from __future__ import annotations

import logging
from typing import Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver

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
    el.click()
    logger.info("%s — clique realizado em id=%s", etapa, element_id)
    espera_minima_seguranca()


def _preencher_id(
    driver: WebDriver,
    element_id: str,
    valor: str,
    logger: logging.Logger,
    etapa: str,
    *,
    log_mascara: str,
) -> None:
    logger.info("%s — aguardando campo id=%s", etapa, element_id)
    el = esperar_id_presente(driver, element_id, descricao=f"{etapa} id={element_id}")
    el.clear()
    el.send_keys(valor)
    logger.info("%s — campo preenchido (%s)", etapa, log_mascara)
    espera_minima_seguranca()


def executar_etapas(
    driver: WebDriver,
    credenciais: CredenciaisCaixa,
    logger: logging.Logger,
) -> None:
    """Executa o fluxo completo no driver já aberto. Não fecha o navegador."""

    # Etapa 1 — Acessar portal
    logger.info("Etapa 1 — Abrindo portal (termos de uso)")
    driver.get(S.URL_TERMOS)
    esperar_id_presente(
        driver,
        S.ID_BOTAO_SIM,
        timeout=S.TIMEOUT_PADRAO,
        descricao="Etapa 1 — página termos (botaosim)",
    )
    logger.info("Etapa 1 — Página carregada")

    # Etapa 2 — Aceitar termos
    _clicar_id(driver, S.ID_BOTAO_SIM, logger, "Etapa 2 — Aceitar termos")

    # Etapa 3 — Acessar
    _clicar_id(driver, S.ID_BOTAO_SIM, logger, "Etapa 3 — Acessar")

    # Etapa 4 — CPF
    _preencher_id(
        driver,
        S.ID_CAMPO_CPF,
        credenciais.cpf,
        logger,
        "Etapa 4 — Informar CPF",
        log_mascara=f"CPF informado: {mascarar_cpf(credenciais.cpf)}",
    )

    # Etapa 5 — Confirmar CPF
    _clicar_id(driver, S.ID_BOTAO_ENVIAR_CPF, logger, "Etapa 5 — Confirmar CPF")

    # Etapa 6 — Solicitar código
    _clicar_id(driver, S.ID_BOTAO_LOGIN, logger, "Etapa 6 — Solicitar código por e-mail")

    # Etapa 7 — Aguardar código (manual) + Enviar (manual)
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

    # Etapa 8 — Senha
    logger.info("Etapa 8 — Senha carregada com sucesso (valor omitido no log)")
    el_senha = esperar_id_presente(
        driver,
        S.ID_CAMPO_SENHA,
        descricao="Etapa 8 — campo password",
    )
    el_senha.clear()
    el_senha.send_keys(credenciais.senha)
    logger.info("Etapa 8 — Campo senha preenchido")
    espera_minima_seguranca()

    # Etapa 9 — Entrar
    logger.info("Etapa 9 — Aguardando botão Entrar (%s)", S.CSS_BOTAO_ENTRAR)
    btn = esperar_clicavel(
        driver,
        By.CSS_SELECTOR,
        S.CSS_BOTAO_ENTRAR,
        descricao="Etapa 9 — button[tabindex='1']",
    )
    btn.click()
    logger.info("Etapa 9 — Clique em Entrar realizado")
    logger.info(
        "Automação de login ENCERRADA. Continue a navegação manualmente no Edge."
    )


def executar_login_automatizado(
    *,
    manter_navegador_aberto: bool = True,
    driver: Optional[WebDriver] = None,
    credenciais: Optional[CredenciaisCaixa] = None,
) -> WebDriver:
    """
    Ponto de entrada público.

    Returns:
        WebDriver do Edge (sessão ativa para uso manual ou integração futura).
    """
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
