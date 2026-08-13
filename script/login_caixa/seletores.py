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

# "Vincular Dispositivo" — hidden input id=vincular; botão Sim chama vincularDisp('true')
ID_VINCULAR = "vincular"
BOTAO_VINCULAR_SIM_CANDIDATOS = (
    ("css", "button[onclick*=\"vincularDisp('true')\"]"),
    ("css", 'button[onclick*="vincularDisp(\'true\')"]'),
    ("xpath", "//button[@name='login' and contains(@onclick,\"vincularDisp('true')\")]"),
    ("xpath", "//button[contains(@onclick,'vincularDisp') and contains(normalize-space(.),'Sim')]"),
    ("xpath", "//form[@id='form-login']//div[contains(@class,'button-group')]/button[normalize-space()='Sim']"),
)

# E-mail na Validação de Login (radio name=mail) — já costuma vir marcado
RADIO_EMAIL_CANDIDATOS = (
    ("css", "input[name='mail'][type='radio']"),
    ("css", "div.radio-2fa input[type='radio']"),
    ("xpath", "//input[@name='mail' and @type='radio']"),
)

# Botão "Receber código" (Validação de Login) — NÃO confundir com Sim do Vincular
BOTAO_RECEBER_CODIGO_CANDIDATOS = (
    ("xpath", "//button[@name='login' and contains(normalize-space(.),'Receber')]"),
    ("xpath", "//button[contains(normalize-space(.),'Receber código') or contains(normalize-space(.),'Receber codigo')]"),
    ("xpath", "//form[@id='form-login']//div[contains(@class,'button-group')]/button[@name='login' and not(contains(@onclick,'vincularDisp'))]"),
    ("css", "#form-login div.button-group > button[name='login']"),
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
