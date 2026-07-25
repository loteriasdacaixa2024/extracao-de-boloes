# -*- coding: utf-8 -*-
"""Logger do login automatizado — sem CPF completo, senha, OTP, cookies ou tokens."""

from __future__ import annotations

import logging
from pathlib import Path


def configurar_logger(nome: str = "login_caixa") -> logging.Logger:
    logger = logging.getLogger(nome)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    # Log em arquivo local (sem dados sensíveis — o código já mascara)
    logs_dir = Path(__file__).resolve().parents[2] / "logs"
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        arquivo = logging.FileHandler(
            logs_dir / "login_caixa.log", encoding="utf-8"
        )
        arquivo.setFormatter(fmt)
        logger.addHandler(arquivo)
    except OSError:
        logger.warning("Não foi possível criar arquivo de log em logs/; só console.")

    logger.propagate = False
    return logger
