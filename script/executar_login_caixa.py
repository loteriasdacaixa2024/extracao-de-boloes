# -*- coding: utf-8 -*-
"""
Atalho para executar o login automatizado sem alterar outros scripts.

Uso (na pasta script):
  python executar_login_caixa.py

Credenciais: config.local.json na raiz do projeto (ver config.local.json.example).
"""
from __future__ import annotations

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from login_caixa.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
