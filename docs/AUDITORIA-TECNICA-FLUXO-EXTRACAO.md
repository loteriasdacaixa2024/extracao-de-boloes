# Auditoria Técnica Completa do Fluxo de Extração

**Projeto:** `I:\Meu Drive\extracao-de-boloes`  
**Versão do extrator no código:** `CHECKPOINT-RESUME-v3.2 (varredura 27 UFs)`  
**Arquivo principal:** `script/baixar_boloes-API.py`  
**Data da auditoria:** 2026-08-04  
**Escopo:** somente leitura e documentação do comportamento atual. Nenhum arquivo de código foi modificado.

---

## 1. Objetivo

Documentar exatamente como o sistema funciona hoje, com base no código-fonte e no fluxo de execução, para permitir comparação com o comportamento desejado antes de qualquer alteração.

---

## 2. Visão geral da arquitetura

| Papel | Arquivo / pasta |
|---|---|
| Launcher Windows | `iniciar_servidores_boloes.bat` |
| Entrada do extrator | `script/baixar_boloes-API.py` (`main` → `menu_principal`) |
| Login automatizado | `script/login_caixa/` (`fluxo.py`, `credenciais.py`, `driver_edge.py`, …) |
| Atalho isolado de login | `script/executar_login_caixa.py` |
| Checkpoint / pausa | `script/boloes_checkpoint.py` |
| Estados (UF) | `script/boloes_estados.py` |
| Filtros, paginação, lotérica | `script/boloes_filtro_loterica.py` |
| Interceptação API / Detalhes | `script/boloes_api_caixa.py` |
| Parse dos JSONs da API | `script/boloes_api_parser.py` |
| Gravação / merge do JSON | `script/boloes_consolidar.py` |
| Modalidades | `script/boloes_modalidades.py` |
| JSON de sessão | `json-boloes/boloes_{concurso}_{modalidade}.json` |
| Checkpoint em disco | `json-boloes/_checkpoint_extracao.json` |
| Pedido de pausa | `json-boloes/_PAUSE.request` |
| Capturas brutas da API | `capturas-api/api_r{rodada}_p{pagina}_{timestamp}.json` |
| Credenciais | `config.local.json` (raiz; gitignored; exemplo em `config.local.json.example`) |

Scripts paralelos / legado (não são o fluxo principal do `.bat`):

- `script/baixar_boloes-POR-PAGINA-CONFIRMACAO.py`
- `script/baixar_boloes-DETECTA-LOOP.py`
- `script/baixar_boloes-DANDO-LOOP.py`
- `script/baixar_boloes-API - Copia.py`

O launcher oficial aponta **somente** para `baixar_boloes-API.py`.

---

## 3. Arquivo inicial da aplicação

### 3.1 Via Windows Terminal (uso típico)

1. Usuário executa `iniciar_servidores_boloes.bat` na raiz do projeto.
2. O `.bat` resolve a pasta do servidor Flask (`app.py` ou `servidor.py` + venv), nesta ordem:
   - pasta pai de `extracao-de-boloes`
   - `D:\Loterias\AnalisePorPosicao-DiaDeSorte-Only`
   - `..\LoteriasBoloesDaSorte`
   - `I:\Meu Drive\LoteriasBoloesDaSorte`
   - `D:\Loterias\LoteriasBoloesDaSorte`
3. Define `LOGIN_CAIXA_AUTO=1`.
4. Abre o Windows Terminal com **dois painéis**:
   - Painel 1: servidor Flask (`python app.py` / `servidor.py`)
   - Painel 2: extrator  
     `"%PYTHON_EXE%" -u "%PASTA_BOLOES%\script\baixar_boloes-API.py"`

### 3.2 Via Python direto

```text
python -u script/baixar_boloes-API.py
```

Nesse caso, login automático só ocorre se:

- `LOGIN_CAIXA_AUTO` estiver em `1/true/sim/yes/on`, **ou**
- `config.local.json` tiver `"login_automatico": true`.

### 3.3 Ponto de entrada Python

```text
if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        ...
    finally:
        fechar_navegador()
```

`main()` (`baixar_boloes-API.py`):

1. Imprime banner com `VERSAO_EXTRATOR`.
2. Chama `_carregar_config_inicio()` (cache opcional de filtro lotérica).
3. Entra em `menu_principal()` (loop infinito até CTRL+C).

**Importante:** o `.bat` **não** escolhe a opção `[1]` automaticamente. Após iniciar, o usuário ainda precisa digitar `1` no menu.

---

## 4. Fluxo de inicialização (cronológico)

### Fase A — Antes do navegador

| Passo | O quê | Onde |
|---|---|---|
| A1 | Criação das pastas `json-boloes/` e `capturas-api/` (no import do módulo) | topo de `baixar_boloes-API.py` |
| A2 | Banner + carga de cache de config | `main()` → `_carregar_config_inicio()` |
| A3 | Menu interativo | `menu_principal()` |
| A4 | Usuário escolhe `[1]` | `extrair_automatico()` |
| A5 | Pergunta modalidade (1–9 / especiais / ENTER=auto) | `_coletar_modalidade_pre_extracao()` |
| A6 | Pergunta concurso (número / ENTER=auto) | `_coletar_concurso_pre_extracao()` |
| A7 | Monta filtro padrão: qualquer lotérica, `qtd_dezenas=None` | `cfg_qualquer_loterica(None)` |
| A8 | Exibe resumo pré-Edge | `_exibir_resumo_pre_extracao()` |

### Fase B — Abertura do Edge e login

| Passo | O quê | Onde |
|---|---|---|
| B1 | `webdriver.Edge` com `page_load_strategy=eager`, `detach=True` | `iniciar_navegador()` |
| B2 | Instala hook CDP/JS de interceptação API | `instalar_interceptador_api()` em `boloes_api_caixa.py` |
| B3 | Se login auto habilitado → `executar_login_automatizado(driver, manter_navegador_aberto=True)` | `login_caixa/fluxo.py` |
| B4 | Após login (ou falha), tenta abrir URL de bolões se necessário | `URL_BOLOES` |
| B5 | Mensagem: usuário deve escolher modalidade/filtros **manualmente** no Edge | `iniciar_navegador()` |

### Fase C — Pausa rígida (interação obrigatória)

| Passo | O quê | Onde |
|---|---|---|
| C1 | `SESSAO_AUTORIZADA = False` | `extrair_automatico()` |
| C2 | Loop até digitar `SIM` / `S` / `OK` / `INICIAR` | `aguardar_site_pronto()` → `_ler_confirmacao_sim()` |
| C3 | Enter vazio **não** inicia | `_ler_confirmacao_sim()` / validação em `aguardar_site_pronto()` |
| C4 | Exige login detectado (`_usuario_logado_caixa`) | |
| C5 | Exige ≥1 botão Detalhes visível | `aguardar_detalhes_visiveis(..., minimo=1, timeout=12)` |
| C6 | Só então `SESSAO_AUTORIZADA = True` | `extrair_automatico()` |

### Fase D — Extração automática (27 UFs)

| Passo | O quê | Onde |
|---|---|---|
| D1 | Resolve modalidade (terminal ou site) | `_modalidade_extracao()` |
| D2 | Gera nome base `boloes_{concurso}_{modalidade}` | `gerar_arquivo_base()` |
| D3 | Monta fila SP → demais UFs A–Z | `estados_varredura('SP')` |
| D4 | Lê checkpoint; monta `ufs_concluidas`, `uf_retomar`, `pagina_retomar` | `carregar_checkpoint()` |
| D5 | Para cada UF pendente: aplica filtro no site + loop de páginas | `aplicar_filtro_varredura_automatica()` + `_loop_extracao_paginas()` |

### Fase E — Encerramento

| Passo | O quê | Onde |
|---|---|---|
| E1 | Retorno de `extrair_automatico()` ao menu | `menu_principal()` continua |
| E2 | Fechamento do Edge só em CTRL+C / saída do processo | `finally: fechar_navegador()` |
| E3 | Com `detach=True`, o Edge pode permanecer aberto mesmo após `quit` falhar | `iniciar_navegador()` |

---

## 5. Inicialização automática de serviços

O `.bat` sobe **dois** processos:

1. **Servidor Flask** (painel 1) — usado pelo ecossistema de bolões/conferências; o extrator Selenium **não** chama esse servidor no fluxo de captura da API Caixa.
2. **Extrator** (painel 2) — processo principal desta auditoria.

Não há auto-start da opção `[1]` do menu. Há auto-habilitação do login (`LOGIN_CAIXA_AUTO=1`).

---

## 6. Processo de autenticação (detalhado)

Arquivo: `script/login_caixa/fluxo.py` → `executar_login_automatizado()` → `executar_etapas()`.

Credenciais: `script/login_caixa/credenciais.py` lê `config.local.json` ou `credentials.json` na raiz.

| Etapa | Ação | Interação humana? |
|---|---|---|
| 1 | Abre página de termos (`S.URL_TERMOS`) | Não |
| 2 | Clica Sim (+18) | Não |
| 3 | Clica Acessar | Não |
| 4 | Localiza input CPF e digita CPF | Não |
| 5 | Confirma CPF / Próximo | Não |
| 6 | Clica “Receber código” | Não |
| 7a | Aguarda campo de código; **usuário digita o código do e-mail** | **Sim** |
| 7b | Automação **não** clica em Enviar; **usuário clica Enviar manualmente** | **Sim** |
| 8 | Detecta campo senha; digita senha | Não |
| 9 | Clica Entrar | Não |
| 10 | Aguarda redirect OAuth de volta ao portal (`_aguardar_retorno_portal_logado`) | Não (espera passiva) |

Após o módulo de login:

- Se parecer logado e URL não for `bolao-caixa` → `driver.get(URL_BOLOES)`.
- Se login auto falhar (`LoginAutomatizadoError` ou Exception) → mensagem para continuar **manualmente**; o extrator **não aborta**.

Confirmação posterior de sessão (antes de extrair):

- `_usuario_logado_caixa()`: texto “Olá/Sair/Minha conta”, tokens no `localStorage`, ou captura API `recuperar-dados`.
- `aguardar_site_pronto()` exige login + Detalhes visíveis + `SIM`.

---

## 7. Processo de navegação

### 7.1 URL alvo

`https://www.loteriasonline.caixa.gov.br/silce-web/#/bolao-caixa`

### 7.2 Modalidade e filtros no site (modo [1])

Após o login, a **modalidade no site é escolhida pelo usuário** (mensagem explícita em `iniciar_navegador` / `aguardar_site_pronto`).

Quando a varredura por UF começa, o script tenta aplicar sozinho:

`aplicar_filtro_varredura_automatica(driver, cfg, mod, estado)`:

1. `selecionar_modalidade_bolao` (força modalidade no filtro do site)
2. Limpa campo de lotérica (se houver texto)
3. `selecionar_estado_bolao` (dropdown/select/xpath/JS por nome, sigla ou IBGE)
4. Se `cfg.qtd_dezenas` truthy → `_selecionar_qtd_dezenas`
5. `_clicar_aplicar` → aguarda lista

**Comportamento atual relevante:** em `extrair_automatico()`, o cfg é `cfg_qualquer_loterica(None)`, portanto `qtd_dezenas is None` e a etapa de dezenas **não é aplicada** no site. Existe `cfg_varredura_automatica(mod)`, mas **não é chamada** por esse fluxo.

Se o filtro de UF falhar, o código **avisa e tenta extrair mesmo assim**.

### 7.3 Troca de páginas (dentro de uma UF)

Em `_capturar_pagina_atual` / `_loop_extracao_paginas`:

- Página 1: não clica Seguinte; aguarda Detalhes.
- Páginas > 1 (modo automático):
  1. `ir_proxima_pagina_lista` (botão Seguinte)
  2. Se falhar e for última página → retorno `-2` (fim)
  3. Fallback: `ir_para_pagina_lista` (Angular / número)
- Retomada: `ir_direto_para_pagina_lista` (salto Angular/número, sem Seguinte passo a passo); se UI não bater, pede OK/FORCAR ao usuário.

Critério de última página:

- `ultima_pagina_detectada(driver)` / `eh_ultima_pagina(driver)` — botão Seguinte desabilitado/ausente.
- Mensagem: `MSG_ULTIMA_PAGINA`.

### 7.4 Troca entre estados (UFs)

Ordem em `boloes_estados.estados_varredura('SP')`:

```text
SP → AC → AL → AM → AP → BA → CE → DF → ES → GO → MA → MG → MS → MT →
PA → PB → PE → PI → PR → RJ → RN → RO → RR → RS → SC → SE → TO
```

Para cada UF pendente (não listada em `ufs_concluidas` do checkpoint):

1. Aplica filtro da UF no site.
2. Roda `_loop_extracao_paginas` com `uf_varredura=sigla`.
3. Decide se marca UF concluída / pausa / interrompe (ver §12).

---

## 8. Processo de extração (por página)

Função central: `_capturar_pagina_atual` → `detalhar_pagina_ate_esperado` → `_persistir_json_pagina`.

Sequência por página:

1. Bloqueio se `SESSAO_AUTORIZADA` for False → retorna `-1`.
2. `garantir_sessao_caixa` — se sessão cair, interrompe (`-1`).
3. Navegação para a página (exceto pág. 1 / retomada com `pular_navegacao_proxima`).
4. `aguardar_capturas_api` (lista interceptada).
5. `preparar_pagina_para_detalhes` + `detectar_detalhes_pagina` (meta de botões Detalhes / códigos).
6. `detalhar_pagina_ate_esperado`:
   - até 15 rodadas;
   - clica Detalhes sem depender do popup (`disparar_detalhes_sem_popup`);
   - coleta bolões das capturas API (`coletar_boloes_das_capturas`);
   - a cada chunk, callback `_salvar_tempo_real` grava parcial no JSON;
   - para se meta atingida, sem botões pendentes, ou 3 rodadas sem progresso.
7. `_persistir_json_pagina`:
   - salva captura bruta em `capturas-api/`;
   - parseia bolões (`boloes_de_capturas_api` / fallback driver);
   - filtra modalidade + concurso (`_boloes_para_json_arquivo`);
   - **não** filtra lotérica no arquivo (grava modalidade inteira);
   - merge por `hash_bolao` via `salvar_parcial` → `salvar_json_continuacao`.
8. Atualiza painel, checkpoint, imprime resumo da página.
9. Incrementa `pagina` e repete até fim / pausa / erro.

Códigos de retorno de `_capturar_pagina_atual`:

| Valor | Significado |
|---|---|
| `>= 0` | Quantidade de novos gravados nesta página |
| `-1` | Interrupção (sessão / navegação / filtro) |
| `-2` | Última página detectada ao tentar avançar |

---

## 9. Processo de gravação do JSON

### 9.1 Quando o arquivo é criado

Na primeira chamada bem-sucedida a `salvar_json_continuacao` / `salvar_json_boloes` com bolões que tenham `hash_bolao`.

Caminho típico:

```text
json-boloes/boloes_{concurso}_{modalidade}.json
```

Exemplos de nome:

- Com concurso informado: `boloes_3040_mega-sena.json`
- Sem concurso ainda: `boloes_sem-concurso_{modalidade}.json` → renomeado depois via `_atualizar_arquivo_base_concurso` / `_renomear_json_sessao`

### 9.2 Quando é atualizado

1. **Durante os cliques em Detalhes** — callback `_salvar_tempo_real` (parcial por bloco).
2. **Ao fim de cada página** — `_persistir_json_pagina` → `salvar_parcial`.
3. **Ao fim do loop** — `_recuperar_boloes_das_capturas` pode reprocessar capturas em disco e mesclar de novo.
4. **Continuidade** — se já existir arquivo da mesma sessão, hashes existentes são carregados e novos são mesclados (nunca apaga registros válidos com hash).

Implementação: `boloes_consolidar.salvar_json_continuacao` → `mesclar_listas` (dedupe por `hash_bolao`) → `salvar_json_boloes` (rewrite completo do arquivo com `indent=2`).

### 9.3 Quando é “fechado”

Não há handle permanente aberto. Cada gravação:

1. Lê o JSON existente.
2. Mescla.
3. Reescreve o arquivo inteiro.

Não existe etapa explícita de “fechar arquivo”. O arquivo permanece no disco após o fim da extração / pausa / CTRL+C.

Espelho `*_CONSOLIDADO.json` **não** é mais gravado a cada página no fluxo automático (comentários e código em `_loop_extracao_paginas` / `_iniciar_continuidade_inteligente`). A opção de menu `[3]` ainda consolida capturas via `consolidar_capturas_pasta`.

### 9.4 Capturas auxiliares

Além do JSON de sessão, cada página tenta gravar:

```text
capturas-api/api_r{rodada}_p{pagina}_{unix_ts}.json
```

`rodada_filtro` no modo [1] = índice da UF na fila de pendentes (não o número da página).

---

## 10. Controle de estados, páginas e lotéricas

### 10.1 Estados (UF)

- Lista fixa de 27 UFs com código IBGE em `boloes_estados.py`.
- Progresso de UFs concluídas em `_checkpoint_extracao.json` → campo `ufs_concluidas`.
- UF em andamento → `uf_atual`.
- Retomada: se status ∈ {`Em execução`, `Pausado`}, `pagina_retomar = pagina_atual + 1` na `uf_atual`.

### 10.2 Páginas

- Contador local `pagina` em `_loop_extracao_paginas`.
- Persistido em checkpoint: `pagina_atual`, `proxima_pagina`, `total_paginas` (da API quando disponível).
- Metadados de paginação da API: `ler_metadados_paginacao_api` → `pagina_atual` / `ultima_pagina` / `total_registros`.

### 10.3 Lotéricas

- Modo [1] automático: **qualquer lotérica** (`qualquer_loterica=True`, termo vazio).
- JSON de sessão **não** exclui por lotérica; filtro de lotérica só afeta painel/resumo (`_boloes_do_filtro`).
- Modo [2] manual: usuário aplica filtro no site; script pode ler filtro via `ler_filtro_aplicado_site` ou perguntar no terminal.

---

## 11. Checkpoints existentes

Arquivo: `json/boloes` → `json-boloes/_checkpoint_extracao.json`  
Módulo: `script/boloes_checkpoint.py`

### 11.1 Campos principais

| Campo | Uso |
|---|---|
| `modalidade` / `modalidade_label` | Identificação |
| `concurso` | Concurso alvo |
| `arquivo_base` | Nome do JSON de sessão (sem `.json`) |
| `pagina_atual` | Última página processada com sucesso |
| `proxima_pagina` | `pagina_atual + 1` (0 se Concluído) |
| `total_paginas` | Última página conhecida da API/UI |
| `boloes_extraidos` | Contagem no arquivo |
| `status` | `Em execução` \| `Pausado` \| `Concluído` |
| `uf_atual` | UF em andamento |
| `ufs_concluidas` | Lista de siglas já ok |
| `atualizado_em` | Timestamp |

### 11.2 Quando grava

1. Ao posicionar retomada (antes de extrair a página alvo) — status `Em execução`, `pagina_atual = pagina - 1`.
2. Após cada página bem-sucedida — status `Em execução`.
3. Ao sair do loop da UF — status `Pausado` / `Concluído` / `Em execução` (conforme fim real e se é a última UF pendente).

### 11.3 Pausa

- Tecla `P` (sem Enter) via `msvcrt` em `pause_solicitada`.
- Ou criar arquivo `json-boloes/_PAUSE.request`.
- A pausa só interrompe **depois** de concluir a página atual.
- Instrução impressa por `instruir_pause`.

### 11.4 Retomada

- Em modo UF automático, `forcar_pagina_inicial` é **sempre** passado → **não** chama `perguntar_retomada` (prompt `[C]/[N]`).
- A retomada de página vem do checkpoint (`pagina_atual + 1`) apenas para a `uf_atual`.
- Outras UFs começam na página 1.
- O prompt `[C]/[N]` de `perguntar_retomada` só ocorreria se `_loop_extracao_paginas` fosse chamado sem `forcar_pagina_inicial` (ex.: caminhos manuais/legado).

---

## 12. Tratamento de erros e critérios de parada

### 12.1 Critérios que encerram / interrompem a extração

| Condição | Efeito |
|---|---|
| Usuário digita N/NAO/CANCELAR na pausa SIM | Cancela antes de extrair |
| Login não detectado / 0 Detalhes ao digitar SIM | Mantém pausa (não inicia) |
| Página 1 sem bolões e sem Detalhes | Abort da UF/loop atual |
| Retorno `-2` (Seguinte falhou + última página) | UF concluída (`chegou_ao_fim`) |
| `ultima_pagina_detectada` após página OK | UF concluída |
| Retorno `-1` (sessão/navegação) | Interrompe loop da UF |
| Pausa (`P` / `_PAUSE.request`) | Status `Pausado`; para varredura de UFs |
| CTRL+C | Encerra processo; tenta fechar Edge |
| Todas as 27 UFs em `ufs_concluidas` | Não inicia novas UFs; carrega JSON e retorna |
| UF com `paginas_com_dados == 0` | Marca UF como concluída e **segue** para a próxima |
| Interrupção sem pausa explícita e com dados | `break` da varredura de UFs (não avança para próxima UF) |
| Só a última UF pendente marca checkpoint `Concluído` | `marcar_concluido_ao_fim=eh_ultima_uf` |

### 12.2 Erros “silenciosos” ou não fatais

| Situação | Comportamento |
|---|---|
| Falha no login automático | Log + continua para login manual |
| Falha ao abrir URL bolões | Aviso; segue |
| Filtro de UF não aplicado | Aviso; tenta extrair mesmo assim |
| Captura bruta falha ao salvar | Retorna `None`; tenta fallback pelo driver |
| Bolão sem `hash_bolao` | Aviso; **não entra** no JSON (`mesclar_listas` ignora) |
| Página incompleta (gravados < esperados) | Aviso; **não** para a extração |
| Página igual à anterior (hashes) | Aviso; continua |
| Exceções no menu | Print + traceback; menu continua |
| CDP hook falha | `pass` em `instalar_interceptador_api` |

---

## 13. Etapas que dependem da interação do usuário

1. Escolher `[1]` no menu (não é automático pelo `.bat`).
2. Informar modalidade e concurso no terminal (ou ENTER para auto).
3. No login auto: digitar código do e-mail no Edge.
4. No login auto: clicar **Enviar** manualmente no Edge.
5. No Edge: confirmar login; escolher modalidade/filtros iniciais se quiser.
6. Digitar **SIM** no terminal (Enter vazio não inicia).
7. Em retomada com salto de página falho: OK / FORCAR no terminal.
8. Opcional: tecla **P** para pausar.
9. Modo `[2]`: ENTER a cada página / FIM / novo filtro.

---

## 14. Sequência cronológica completa (do start ao fim)

```text
1.  iniciar_servidores_boloes.bat
2.  Resolve RAIZ do servidor Flask + PYTHON_EXE
3.  Define LOGIN_CAIXA_AUTO=1
4.  Abre wt.exe:
      - painel SERVIDOR → python app.py/servidor.py
      - painel EXTRATOR → python -u script/baixar_boloes-API.py
5.  baixar_boloes-API.py:
      - cria pastas json-boloes / capturas-api
      - main() imprime versão
      - _carregar_config_inicio()
      - menu_principal()
6.  Usuário digita 1
7.  extrair_automatico():
      - _coletar_modalidade_pre_extracao()
      - _coletar_concurso_pre_extracao()
      - cfg = qualquer lotérica, qtd_dezenas=None
      - _exibir_resumo_pre_extracao()
8.  iniciar_navegador():
      - Edge abre
      - instalar_interceptador_api()
      - se LOGIN_CAIXA_AUTO: executar_login_automatizado()
          etapas 1–6 automáticas
          etapa 7: usuário código + Enviar
          etapas 8–9 senha + Entrar
          aguarda OAuth
      - navega para Bolões se necessário
9.  PAUSA: aguardar_site_pronto() até SIM + login + Detalhes
10. SESSAO_AUTORIZADA = True
11. Resolve modalidade/concurso/arquivo_base
12. estados_varredura('SP'); carrega checkpoint
13. Para cada UF pendente:
      a. aplicar_filtro_varredura_automatica(UF)
      b. _loop_extracao_paginas(...):
           - limpa capturas API em memória
           - continuidade de hashes do JSON existente
           - posiciona página (1 ou retomada)
           - loop:
               * _capturar_pagina_atual
               * cliques Detalhes + interceptação API
               * salvar_parcial (JSON tempo real)
               * salvar_checkpoint
               * se pausa / última página / erro → sai
           - recuperação opcional das capturas em disco
           - checkpoint final da UF
      c. Se Pausado → para varredura
      d. Se UF ok ou vazia → marca ufs_concluidas e segue
      e. Se interrompida com dados e sem pausa → break
14. Renomeia/valida JSON final se houver bolões
15. Retorna ao menu_principal (Edge ainda aberto)
16. CTRL+C → fechar_navegador() → "Fim!"
```

---

## 15. Fluxograma textual completo

```text
[iniciar_servidores_boloes.bat]
        |
        +---> [Servidor Flask] (paralelo; fora do loop de extração)
        |
        v
[python baixar_boloes-API.py]
        |
        v
[main] --> [_carregar_config_inicio] --> [menu_principal]
        |
        +-- [0] fechar_navegador --> menu
        +-- [2] extrair_sessao_multi_filtros (manual/ENTER)
        +-- [3] consolidar capturas-api
        +-- [M]/[M1-9]/especiais] só ajustam parser
        |
        v
      [1] extrair_automatico
        |
        v
[perguntar modalidade] --> [perguntar concurso] --> [resumo]
        |
        v
[driver is None?] --sim--> [iniciar_navegador]
        |                         |
        |                         +--> Edge + hook API
        |                         +--> login auto? --sim--> [login_caixa.fluxo]
        |                         |                              |
        |                         |                              +--> CPF/senha auto
        |                         |                              +--> CÓDIGO + ENVIAR (humano)
        |                         |                              +--> Entrar + OAuth
        |                         +--> abre URL bolões se preciso
        |                         +--> instrui: modalidade MANUAL no Edge
        v
[SESSAO_AUTORIZADA=False]
        |
        v
[aguardar_site_pronto]
        |
        +-- Enter vazio -----> permanece pausado
        +-- N/CANCELAR ------> return ([], None) --> menu
        +-- SIM sem login ----> permanece pausado
        +-- SIM sem Detalhes -> permanece pausado
        +-- SIM ok ----------> segue
        v
[SESSAO_AUTORIZADA=True]
[gerar arquivo_base] [fila 27 UFs] [ler checkpoint]
        |
        v
{ todas UFs em ufs_concluidas? } --sim--> carrega JSON --> return --> menu
        |
       nao
        v
+------------------ PARA CADA UF PENDENTE ------------------+
|  aplicar_filtro_varredura_automatica(UF)                  |
|        | (falha? avisa e continua)                        |
|        v                                                  |
|  _loop_extracao_paginas                                   |
|        |                                                  |
|        +--> continuidade JSON/hashes                      |
|        +--> forcar pagina (retomada ou 1)                 |
|        +--> reset_pause_flags / instruir_pause            |
|        |                                                  |
|        +======== ENQUANTO True ==================+        |
|        |  _capturar_pagina_atual                 |        |
|        |     |                                   |        |
|        |     +-- SESSAO? garantir_sessao         |        |
|        |     +-- navegar página (Seguinte/etc)   |        |
|        |     +-- detectar Detalhes               |        |
|        |     +-- detalhar_pagina_ate_esperado    |        |
|        |     |      +-- callback salvar parcial  |        |
|        |     +-- _persistir_json_pagina          |        |
|        |     |      +-- capturas-api/*.json      |        |
|        |     |      +-- merge json-boloes/*.json |        |
|        |     v                                   |        |
|        |  n_novos == -2 ? --> fim UF (última)    |        |
|        |  n_novos < 0   ? --> interrompe UF      |        |
|        |  pág1 vazia s/ Detalhes? --> abort UF   |        |
|        |  salvar_checkpoint (Em execução)        |        |
|        |  pause_solicitada? --> Pausado; break   |        |
|        |  ultima_pagina_detectada? --> fim UF    |        |
|        |  pagina += 1                            |        |
|        +=========================================+        |
|        |                                                  |
|        +--> recuperar capturas disco (opcional)           |
|        +--> checkpoint final UF                           |
|                                                           |
|  status Pausado? ------------------> break UFs            |
|  uf_concluida / Concluído? --------> append ufs_concluidas|
|  paginas_com_dados==0? ------------> marca UF ok; segue   |
|  senão (interrompeu com dados) ----> break UFs            |
+-----------------------------------------------------------+
        |
        v
[renomear/validar JSON] --> return --> [menu_principal]
        |
       CTRL+C
        v
[fechar_navegador] --> Fim!
```

---

## 16. Comportamentos inesperados / divergentes (código atual)

Estes pontos **não são correções** — são observações para comparar com o comportamento desejado.

### 16.1 `cfg_varredura_automatica` não é usado no modo [1]

`extrair_automatico` usa `cfg_qualquer_loterica(None)` em vez de `cfg_varredura_automatica(mod)`.

Efeitos:

- `qtd_dezenas=None` → não seleciona quantidade de dezenas no filtro do site.
- Aceita bolões com qualquer quantidade de dezenas no JSON (filtro de dezenas no arquivo também não aplica, pois o JSON filtra só modalidade+concurso).

### 16.2 Versão divergente entre `.bat` e código

- `.bat` imprime `CHECKPOINT-RESUME-v3.1`
- Código: `CHECKPOINT-RESUME-v3.2 (varredura 27 UFs)`

### 16.3 Docstring desatualizada vs. pausa real

Comentários/docstrings ainda citam “ENTER” para iniciar; o código exige **SIM** (`aguardar_site_pronto` / `_ler_confirmacao_sim`).

### 16.4 Prompt de retomada `[C]/[N]` contornado no modo automático

Como `forcar_pagina_inicial` é sempre passado na varredura por UF, `perguntar_retomada` **não** é executada nesse fluxo. A retomada é silenciosa a partir do checkpoint.

### 16.5 UF sem dados é marcada como concluída

Se uma UF termina com `paginas_com_dados == 0` (filtro errado, lista vazia, abort na pág. 1), ela entra em `ufs_concluidas` e **não será revisitada** até o checkpoint ser limpo/alterado.

### 16.6 Interrupção mid-UF sem pausa explícita

Se o loop da UF quebra por sessão/navegação (`n_novos < 0`) ou abort pág. 1 **com** algum dado em outras páginas da mesma rodada, o `else` em `extrair_automatico` faz `break` e **não** avança para a próxima UF.

### 16.7 `STATUS_CONCLUIDO` só na última UF pendente

UFs intermediárias, mesmo “ok”, gravam checkpoint com status `Em execução` (`marcar_concluido_ao_fim=False`), mantendo `ufs_concluidas` no `extra`. Isso é intencional para retomada, mas o status global “Concluído” só aparece ao terminar a última UF da fila pendente.

### 16.8 Hardcode `total_paginas=218` na retomada inicial

Ao posicionar retomada, o primeiro `salvar_checkpoint` usa `total_paginas=218` fixo até a API informar o valor real nas páginas seguintes.

### 16.9 Servidor Flask paralelo não participa da captura

O extrator captura a API Caixa via Selenium/CDP no Edge. O servidor iniciado pelo `.bat` é independente desse loop; se estiver offline, a extração Selenium ainda pode rodar.

### 16.10 Função `configurar_loterica` duplicada no mesmo arquivo

Há duas definições de `configurar_loterica` em `baixar_boloes-API.py` (aprox. linhas 569 e 1975). Em Python, a segunda sobrescreve a primeira. Comportamento atual = a segunda definição.

### 16.11 Edge com `detach=True`

Mesmo chamando `fechar_navegador()` no `finally`, a opção `detach` pode manter o Edge aberto; o processo Python encerra independentemente.

### 16.12 JSON nunca é “fechado”; risco de escrita parcial

`salvar_json_boloes` escreve direto no path (não usa o mesmo padrão tmp+replace do checkpoint). Interrupção no meio da escrita pode corromper o JSON; há tentativa de reparo em `_carregar_json_reparado`.

### 16.13 Checkpoint atual de exemplo (estado real do disco na auditoria)

Arquivo `json-boloes/_checkpoint_extracao.json` observado:

- modalidade Mega-Sena, concurso 3040
- `pagina_atual: 7`, `proxima_pagina: 8`, `total_paginas: 239`
- `status: Em execução`
- `uf_atual: SP`
- **sem** campo `ufs_concluidas` (ainda só SP em andamento)

Isso confirma o fluxo de checkpoint por página durante a primeira UF.

---

## 17. Mapa rápido: função → responsabilidade

| Função | Arquivo | Papel |
|---|---|---|
| `main` / `menu_principal` | `baixar_boloes-API.py` | Bootstrap + menu |
| `extrair_automatico` | `baixar_boloes-API.py` | Fluxo [1] completo |
| `iniciar_navegador` | `baixar_boloes-API.py` | Edge + hook + login auto |
| `executar_login_automatizado` | `login_caixa/fluxo.py` | Autenticação |
| `aguardar_site_pronto` | `baixar_boloes-API.py` | Gate SIM + login + Detalhes |
| `aplicar_filtro_varredura_automatica` | `boloes_filtro_loterica.py` | Modalidade + UF + Aplicar |
| `_loop_extracao_paginas` | `baixar_boloes-API.py` | Loop páginas + checkpoint |
| `_capturar_pagina_atual` | `baixar_boloes-API.py` | Uma página ponta a ponta |
| `detalhar_pagina_ate_esperado` | `boloes_api_caixa.py` | Cliques Detalhes + coleta API |
| `salvar_parcial` / `salvar_json_continuacao` | API + `boloes_consolidar.py` | Persistência JSON |
| `salvar_checkpoint` / `pause_solicitada` | `boloes_checkpoint.py` | Retomada / pausa |
| `estados_varredura` | `boloes_estados.py` | Ordem das 27 UFs |
| `ir_proxima_pagina_lista` / `ir_direto_para_pagina_lista` | `boloes_filtro_loterica.py` | Paginação |

---

## 18. Conclusão da auditoria

O sistema atual é um extrator Selenium + interceptação de API, orquestrado por menu em `baixar_boloes-API.py`, tipicamente lançado por `iniciar_servidores_boloes.bat` com login automático habilitado.

Fluxo real do modo `[1]`:

1. Terminal coleta modalidade/concurso.
2. Edge abre; login parcialmente automático (código e Enviar manuais).
3. Usuário prepara a lista no site e digita **SIM**.
4. Script varre 27 UFs (SP primeiro), pagina cada UF até o botão Seguinte acabar, grava JSON em tempo real e checkpoint por página.
5. Pausa/CTRL+C/fim de páginas/erros de sessão encerram ou interrompem conforme as regras da §12.
6. O processo volta ao menu; o arquivo JSON permanece em `json-boloes/` sem etapa formal de fechamento.

Este documento reflete **somente o comportamento implementado hoje**, com referências aos arquivos/funções responsáveis, para servir de base de comparação antes de qualquer modificação.
