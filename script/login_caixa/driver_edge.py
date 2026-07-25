# -*- coding: utf-8 -*-
"""Abertura do Microsoft Edge via Selenium (isolado do extrator)."""

from __future__ import annotations

import logging
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.edge.service import Service


def criar_driver_edge(logger: logging.Logger | None = None) -> webdriver.Edge:
    log = logger or logging.getLogger("login_caixa")
    opts = Options()
    # Mantém navegador aberto se o script encerrar com erro (útil para o operador)
    opts.add_experimental_option("detach", True)

    script_dir = Path(__file__).resolve().parents[1]
    driver_path = script_dir / "msedgedriver.exe"

    try:
        if driver_path.is_file():
            log.info("Usando msedgedriver local: %s", driver_path.name)
            service = Service(executable_path=str(driver_path))
            driver = webdriver.Edge(service=service, options=opts)
        else:
            log.info("msedgedriver.exe não encontrado em script/; Selenium Manager.")
            driver = webdriver.Edge(options=opts)
    except Exception as exc:
        log.error("Falha ao iniciar Edge: %s", exc)
        raise

    driver.maximize_window()
    log.info("Edge iniciado com sucesso.")
    return driver
