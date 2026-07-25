# -*- coding: utf-8 -*-
"""
Automação isolada do login inicial no portal Loterias Online Caixa.

Não altera extratores, APIs nem fluxos existentes.
Credenciais vêm apenas de arquivo local (.gitignore).
"""

from .fluxo import executar_login_automatizado

__all__ = ["executar_login_automatizado"]
