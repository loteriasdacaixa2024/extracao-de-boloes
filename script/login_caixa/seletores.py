# -*- coding: utf-8 -*-
"""
Seletores do fluxo de login — ÚNICO ponto de manutenção se a Caixa mudar IDs.
"""

# URL inicial do portal
URL_TERMOS = "https://www.loteriasonline.caixa.gov.br/silce-web/#/termos-de-uso"

# IDs (prioridade)
ID_BOTAO_SIM = "botaosim"
ID_CAMPO_CPF = "template"
ID_BOTAO_ENVIAR_CPF = "button-submit"
ID_BOTAO_LOGIN = "login"
ID_CAMPO_CODIGO = "codigo"
ID_CAMPO_SENHA = "password"

# Sem ID disponível — botão Entrar (após senha)
CSS_BOTAO_ENTRAR = "button[tabindex='1']"

# Tempos (segundos)
TIMEOUT_PADRAO = 60
TIMEOUT_CODIGO_MANUAL = 600  # até 10 min aguardando operador (ajustável)
ESPERA_MINIMA_POS_CLIQUE = 5
POLL_INTERVALO = 0.5
