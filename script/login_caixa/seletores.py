# -*- coding: utf-8 -*-
"""
Seletores do fluxo de login — ÚNICO ponto de manutenção se a Caixa mudar IDs.
"""

# URL inicial do portal
URL_TERMOS = "https://www.loteriasonline.caixa.gov.br/silce-web/#/termos-de-uso"

# IDs (prioridade)
ID_BOTAO_SIM = "botaosim"          # "Você tem mais de 18 anos?" → Sim
ID_BOTAO_ACESSAR = "btnLogin"      # Header "Acessar" (ng-click=autenticar)
# login.caixa.gov.br — campo real do CPF (Inspector confirma)
ID_CAMPO_CPF = "username"
ID_BOTAO_ENVIAR_CPF = "button-submit"
ID_BOTAO_LOGIN = "login"           # legado — na validação o botão tem name=login
ID_CAMPO_CODIGO = "codigo"
ID_CAMPO_SENHA = "password"

# Botão laranja "Receber código" (Validação de Login) — Inspector: name="login"
BOTAO_RECEBER_CODIGO_CANDIDATOS = (
    ("css", "button[name='login']"),
    ("css", "#form-login button[name='login']"),
    ("css", "div.button-group > button[name='login']"),
    ("id", "login"),
    ("xpath", "//button[@name='login']"),
    ("xpath", "//button[contains(normalize-space(.),'Receber')]"),
)

# Só INPUT. Prioridade: id=username (Keycloak Caixa).
# NÃO usar div#template — é apenas o container visual.
CAMPOS_CPF_CANDIDATOS = (
    ("id", "username"),
    ("css", "input#username"),
    ("css", "input[name='username']"),
    ("css", "input[aria-label='CPF']"),
    ("css", "#form-login input[type='text']"),
    ("css", "#template input[type='text']"),
    ("css", "#template input"),
    ("css", "input#cpf"),
    ("css", "input[name='cpf']"),
    ("xpath", "//form[@id='form-login']//input[@type='text' and not(@type='hidden')]"),
    ("xpath", "//input[@id='username' or @name='username' or @aria-label='CPF']"),
)

# Sem ID disponível — botão Entrar (após senha)
CSS_BOTAO_ENTRAR = "button[tabindex='1']"
CSS_BOTAO_ENTRAR_ALT = (
    ("css", "button[tabindex='1']"),
    ("css", "#form-login button[type='submit']"),
    ("css", "button[name='login']"),
    ("xpath", "//button[contains(normalize-space(.),'Entrar')]"),
)

# Domínio do portal após OAuth (não login.caixa)
HOST_PORTAL = "loteriasonline.caixa.gov.br"
TIMEOUT_RETORNO_OAUTH = 120

# Tempos (segundos)
TIMEOUT_PADRAO = 60
TIMEOUT_CODIGO_MANUAL = 600  # até 10 min aguardando operador (ajustável)
ESPERA_MINIMA_POS_CLIQUE = 5
POLL_INTERVALO = 0.5
TIMEOUT_POS_ACESSAR = 90
