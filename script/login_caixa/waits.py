# -*- coding: utf-8 -*-
"""Esperas explícitas (elemento presente / clicável / preenchido)."""

from __future__ import annotations

import time
from typing import Callable

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from . import seletores as S


class ElementoNaoEncontrado(TimeoutException):
    """Elemento não apareceu a tempo."""


def _wait(driver: WebDriver, timeout: float | None = None) -> WebDriverWait:
    return WebDriverWait(driver, timeout or S.TIMEOUT_PADRAO, poll_frequency=S.POLL_INTERVALO)


def esperar_presente(
    driver: WebDriver,
    by: str,
    valor: str,
    *,
    timeout: float | None = None,
    descricao: str = "",
) -> WebElement:
    try:
        return _wait(driver, timeout).until(EC.presence_of_element_located((by, valor)))
    except TimeoutException as exc:
        rotulo = descricao or f"{by}={valor}"
        raise ElementoNaoEncontrado(
            f"Elemento não encontrado (presente): {rotulo}"
        ) from exc


def esperar_clicavel(
    driver: WebDriver,
    by: str,
    valor: str,
    *,
    timeout: float | None = None,
    descricao: str = "",
) -> WebElement:
    try:
        return _wait(driver, timeout).until(EC.element_to_be_clickable((by, valor)))
    except TimeoutException as exc:
        rotulo = descricao or f"{by}={valor}"
        raise ElementoNaoEncontrado(
            f"Elemento não ficou clicável: {rotulo}"
        ) from exc


def esperar_id_clicavel(
    driver: WebDriver,
    element_id: str,
    *,
    timeout: float | None = None,
    descricao: str = "",
) -> WebElement:
    return esperar_clicavel(
        driver,
        By.ID,
        element_id,
        timeout=timeout,
        descricao=descricao or f"id={element_id}",
    )


def esperar_id_presente(
    driver: WebDriver,
    element_id: str,
    *,
    timeout: float | None = None,
    descricao: str = "",
) -> WebElement:
    return esperar_presente(
        driver,
        By.ID,
        element_id,
        timeout=timeout,
        descricao=descricao or f"id={element_id}",
    )


def espera_minima_seguranca(segundos: float | None = None) -> None:
    """Tempo mínimo adicional após ação (não substitui espera explícita)."""
    time.sleep(segundos if segundos is not None else S.ESPERA_MINIMA_POS_CLIQUE)


def aguardar_campo_preenchido_sem_ler_valor(
    driver: WebDriver,
    element_id: str,
    *,
    timeout: float,
    min_chars: int = 1,
    on_tick: Callable[[], None] | None = None,
) -> None:
    """
    Aguarda o campo ter conteúdo digitado pelo operador.
    NÃO retorna nem registra o valor (OTP/código).
    """

    def _cond(drv: WebDriver) -> bool:
        if on_tick:
            on_tick()
        try:
            el = drv.find_element(By.ID, element_id)
            valor = (el.get_attribute("value") or "").strip()
            return len(valor) >= min_chars
        except Exception:
            return False

    try:
        WebDriverWait(driver, timeout, poll_frequency=S.POLL_INTERVALO).until(_cond)
    except TimeoutException as exc:
        raise ElementoNaoEncontrado(
            f"Timeout aguardando preenchimento manual do campo id={element_id} "
            f"({int(timeout)}s). Operador não informou o código a tempo."
        ) from exc


def aguardar_elemento_aparecer_apos_acao_manual(
    driver: WebDriver,
    element_id: str,
    *,
    timeout: float,
    descricao: str = "",
) -> WebElement:
    """Aguarda próximo passo após ação manual do operador (ex.: clique em Enviar)."""
    return esperar_id_presente(
        driver,
        element_id,
        timeout=timeout,
        descricao=descricao or f"id={element_id} (pós-ação manual)",
    )
