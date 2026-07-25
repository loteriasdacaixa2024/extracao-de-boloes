# -*- coding: utf-8 -*-
"""Mascaramento de dados sensíveis para logs."""


def mascarar_cpf(cpf: str) -> str:
    digitos = "".join(c for c in (cpf or "") if c.isdigit())
    if len(digitos) < 2:
        return "***"
    return f"***.***.***-{digitos[-2:]}"


def mascarar_segredo(_valor: str | None = None) -> str:
    return "***"
