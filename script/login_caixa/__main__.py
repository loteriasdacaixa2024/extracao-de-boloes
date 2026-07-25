# -*- coding: utf-8 -*-
"""
Executa: python -m login_caixa
(a partir da pasta script/)
"""

from __future__ import annotations

import sys

from .fluxo import LoginAutomatizadoError, executar_login_automatizado


def main() -> int:
    print("=" * 60)
    print("  Login automatizado — Loterias Online Caixa")
    print("  Módulo isolado (não altera extratores existentes)")
    print("=" * 60)
    print()
    print("Após solicitar o código:")
    print("  1) Digite o código do e-mail no navegador")
    print("  2) Clique MANUALMENTE em Enviar")
    print("  3) A automação digita a senha e clica em Entrar")
    print("  4) Depois disso, continue manualmente")
    print()
    try:
        executar_login_automatizado(manter_navegador_aberto=True)
        print("\nOK — login automatizado concluído. Navegador permanece aberto.")
        return 0
    except LoginAutomatizadoError as exc:
        print(f"\nFALHA: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelado pelo usuário.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
