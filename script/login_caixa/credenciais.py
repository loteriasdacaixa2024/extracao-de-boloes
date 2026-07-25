# -*- coding: utf-8 -*-
"""Carrega CPF/senha apenas de arquivos locais ou variáveis de ambiente."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


class CredenciaisError(RuntimeError):
    """Credenciais ausentes ou inválidas."""


@dataclass(frozen=True)
class CredenciaisCaixa:
    cpf: str
    senha: str


def _raiz_projeto() -> Path:
    # script/login_caixa/credenciais.py → raiz do repositório
    return Path(__file__).resolve().parents[2]


def _ler_dotenv(caminho: Path) -> dict[str, str]:
    dados: dict[str, str] = {}
    if not caminho.is_file():
        return dados
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        dados[chave.strip()] = valor.strip().strip('"').strip("'")
    return dados


def carregar_credenciais(raiz: Path | None = None) -> CredenciaisCaixa:
    """
    Ordem de busca:
      1) config.local.json
      2) credentials.json
      3) .env (CAIXA_CPF / CAIXA_SENHA)
      4) variáveis de ambiente CAIXA_CPF / CAIXA_SENHA
    """
    base = raiz or _raiz_projeto()
    cpf = ""
    senha = ""

    for nome in ("config.local.json", "credentials.json"):
        caminho = base / nome
        if not caminho.is_file():
            continue
        try:
            raw = json.loads(caminho.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CredenciaisError(f"JSON inválido em {nome}: {exc}") from exc
        if not isinstance(raw, dict):
            raise CredenciaisError(f"{nome} deve ser um objeto JSON.")
        cpf = str(raw.get("cpf") or raw.get("CAIXA_CPF") or cpf).strip()
        senha = str(raw.get("senha") or raw.get("CAIXA_SENHA") or senha).strip()
        if cpf and senha:
            break

    if not cpf or not senha:
        env_file = _ler_dotenv(base / ".env")
        cpf = cpf or env_file.get("CAIXA_CPF", "").strip()
        senha = senha or env_file.get("CAIXA_SENHA", "").strip()

    if not cpf or not senha:
        cpf = cpf or os.environ.get("CAIXA_CPF", "").strip()
        senha = senha or os.environ.get("CAIXA_SENHA", "").strip()

    if not cpf or not senha:
        raise CredenciaisError(
            "Credenciais não encontradas. Crie config.local.json na raiz do projeto "
            '(copie de config.local.json.example) com {"cpf": "...", "senha": "..."} '
            "ou defina CAIXA_CPF / CAIXA_SENHA no .env. "
            "Esses arquivos estão no .gitignore e não devem ir para o GitHub."
        )

    digitos = "".join(c for c in cpf if c.isdigit())
    if len(digitos) != 11:
        raise CredenciaisError("CPF inválido: informe 11 dígitos no arquivo local.")

    return CredenciaisCaixa(cpf=digitos, senha=senha)
