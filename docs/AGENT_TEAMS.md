# Equipes de agentes — guia de referência

Referência para montar equipes de agentes do Claude Code neste repositório. A fonte é a
documentação oficial ([code.claude.com/docs/en/agent-teams](https://code.claude.com/docs/en/agent-teams),
lida em 2026-10-22); as seções "Neste repositório" adaptam aquilo às regras do `CLAUDE.md`.

> **Experimental.** Ligado por `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` no bloco `env` de
> `.claude/settings.local.json` (local, fora do git). Sem a variável, nenhuma equipe é criada.
> Só funciona em sessão interativa: com `claude -p` (inclusive o fluxo do Notion via
> `claude -p`) e no Agent SDK, um subagente nomeado roda como subagente comum.

---

## 1. Equipe, subagente ou sessão única?

Antes de montar uma equipe, confira se uma opção mais leve resolve.

| | Subagentes | Equipe de agentes |
|---|---|---|
| **Contexto** | Janela própria; o resultado volta para quem chamou | Janela própria, totalmente independente |
| **Comunicação** | Devolve resultado (nomeados também trocam mensagens) | Colegas trocam mensagens diretamente |
| **Coordenação** | O agente principal gerencia tudo | Mensagens + lista de tarefas compartilhada |
| **Serve para** | Tarefa focada em que só o resultado importa | Trabalho que exige discussão e contestação |
| **Custo em tokens** | Menor | Bem maior: cada colega é uma instância do Claude |

**Use equipe** quando a exploração em paralelo agrega de verdade:

- **Pesquisa e revisão:** cada colega investiga um aspecto e contesta os achados dos outros.
- **Módulo ou feature nova:** cada colega é dono de uma parte, sem pisar no outro.
- **Depuração com hipóteses concorrentes:** cada um testa uma teoria e tenta derrubar as dos outros.
- **Mudança entre camadas:** frontend, backend e testes, cada um com seu dono.

**Não use equipe** para trabalho sequencial, edição do mesmo arquivo, muita dependência entre
partes ou tarefa de rotina. Aí sessão única ou subagente sai melhor e mais barato.

### Neste repositório

| Tarefa | Escolha |
|---|---|
| Uma `TIE-*` comum (um fix, uma migração, um endpoint) | Sessão única. O fluxo do Notion é **uma tarefa por vez**. |
| Revisar um PR por vários ângulos | Equipe (seção 7.1) |
| Investigar regressão do score/backtest sem causa clara | Equipe de hipóteses (seção 7.2) |
| Feature que cruza `frontend/` + `apps/api` + `src/` | Equipe por camada (seção 7.3) |
| Varrer o código atrás de um padrão | Subagente `Explore` |

---

## 2. Como a equipe funciona

| Componente | Papel |
|---|---|
| **Líder** | A sessão principal. Cria a equipe, distribui tarefas, sintetiza. Fixo pela sessão toda. |
| **Colegas** | Instâncias separadas do Claude Code, cada uma com sua janela de contexto. |
| **Lista de tarefas** | Itens `pending` → `in progress` → `completed`, com dependências. |
| **Caixa de mensagens** | Mensagens entre agentes, entregues automaticamente (sem polling). |

- **Como a equipe nasce:** o líder chama a ferramenta Agent com um `name`. Fork ou
  `isolation` na chamada não vira colega. Não há confirmação. **Efeito colateral:** com a
  variável ligada, qualquer subagente que eu nomear vira colega, mesmo sem você pedir equipe.
  Para voltar ao comportamento antigo, `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=0` (vale sem
  reiniciar).
- **O que o colega recebe:** `CLAUDE.md`, servidores MCP e skills do projeto, e o prompt de
  criação. **O histórico da conversa do líder não vai.** Tudo que importa precisa estar no
  prompt.
- **Nomes:** todo colega tem nome e qualquer um fala com qualquer outro pelo nome. Não há
  broadcast: para avisar todos, uma mensagem por destinatário. Dê nomes previsíveis no pedido
  (`revisor-seguranca`, `backend`, `frontend`).
- **Fim do turno:** quando o colega para, o líder recebe a resposta final dele automaticamente.
  Se parou por erro de API, o líder recebe o erro.
- **Tarefas:** o líder atribui ou o colega pega a próxima livre e desbloqueada (com lock de
  arquivo, sem corrida). Concluir uma tarefa desbloqueia as dependentes.
- **Arquivos de estado:** `~/.claude/teams/session-XXXXXXXX/config.json` (apagado no fim da
  sessão; **não edite nem pré-escreva**) e `~/.claude/tasks/session-XXXXXXXX/` (fica até o
  `cleanupPeriodDays`). Um `.claude/teams/*.json` no projeto **não** é configuração.

### Modelo de cada colega

O primeiro que se aplicar:

1. O modelo citado no prompt para aquele colega.
2. O `model` da definição de subagente usada (`inherit` = modelo do líder).
3. `CLAUDE_CODE_SUBAGENT_MODEL`, se diferente de `inherit`.
4. O modelo atual do líder.

O esforço (effort) é herdado do líder. Modelo e fast mode ficam fixos quando o colega nasce.
`teammateDefaultModel` foi removido: diga o modelo no prompt.

### Permissões

- O colega nasce com o modo de permissão do líder (exceto `dontAsk`). Com
  `--dangerously-skip-permissions` no líder, todos herdam. Dá para mudar o modo de um colega
  depois, nunca na criação.
- **Os pedidos de permissão dos colegas aparecem na sessão do líder:** aprove lá.
- Mensagem de outro agente **não** é você: um colega não aprova permissão em seu nome e não
  repassa a outro uma ação negada. No modo automático, o classificador trata "o usuário
  aprovou" vindo de outro agente como não confiável e pode bloquear a mensagem antes da entrega.

---

## 3. Controles

**Modo de exibição** (`teammateMode` em `~/.claude/settings.json`, ou `claude --teammate-mode`):

- `in-process` (padrão): tudo no terminal principal, funciona em qualquer terminal.
- `auto`: painéis divididos se já estiver no tmux ou no iTerm2 com `it2`; senão, in-process.
- `tmux` / `iterm2`: painéis divididos forçados. Não funciona no terminal do VS Code, no
  Windows Terminal nem no Ghostty.

**No painel de agentes (in-process):**

| Tecla | Ação |
|---|---|
| ↑ / ↓ | Seleciona um colega |
| Enter | Abre a transcrição do colega e permite falar com ele |
| Esc | Limpa a seleção; vendo um colega, interrompe o turno dele |
| `x` | Para o colega selecionado |
| Ctrl+T | Mostra/esconde a lista de tarefas |

- Vendo um colega, texto e skills vão para ele. Comandos embutidos vão para o **líder**:
  `/compact`, `/clear` e `/rewind` pedem confirmação; `/model` e `/fast` não rodam dali.
- Linha de colega ocioso some 30 s depois de todos ficarem ociosos, mas ele continua vivo.
  Com mais de 3 ociosos, o excedente vira uma linha `N idle agents`.

**Planejar antes de implementar:** ponha o líder em plan mode (Shift+Tab) e então peça o
colega. Ele trabalha só em leitura até o plano ficar pronto. O plano é aprovado
automaticamente, a menos que você tenha mudado o modo do colega com Shift+Tab na sessão dele:
aí quem revisa é o líder.

**Encerrar:** "Peça ao colega `pesquisador` para encerrar". Ele pode aceitar ou recusar com
motivo, e termina o que está fazendo antes (pode demorar). Os diretórios da equipe são limpos
no fim da sessão.

**Hooks de qualidade** (em `settings.json`; código de saída 2 = devolve o feedback):

| Hook | Quando roda | Exit 2 |
|---|---|---|
| `TeammateIdle` | Colega prestes a ficar ocioso | Mantém o colega trabalhando |
| `TaskCreated` | Tarefa sendo criada | Impede a criação |
| `TaskCompleted` | Tarefa sendo concluída | Impede a conclusão |

---

## 4. Boas práticas

1. **Contexto completo no prompt de criação.** Caminhos, objetivo, restrições, o que já foi
   descartado e o formato da entrega. O colega não viu a conversa.
2. **3 a 5 colegas.** Custo cresce linearmente, coordenação cresce mais rápido. Três focados
   rendem mais que cinco dispersos. Com 15 tarefas independentes, comece com 3.
3. **Tarefas do tamanho certo.** Pequena demais, a coordenação custa mais que o ganho; grande
   demais, o colega passa tempo demais sem prestar contas. Bom: uma unidade com entrega clara
   (uma função, um arquivo de teste, uma revisão). **5 a 6 tarefas por colega** permitem
   redistribuir se alguém travar.
4. **Cada colega é dono de arquivos diferentes.** Dois editando o mesmo arquivo = sobrescrita.
5. **Comece por pesquisa e revisão**, que não escrevem código, antes de implementação em
   paralelo.
6. **Ângulos distintos.** Dê a cada um uma lente que não se sobreponha (segurança × desempenho ×
   testes).
7. **Debate contra ancoragem.** Em depuração, peça explicitamente que um tente derrubar a
   hipótese do outro. A teoria que sobrevive é a provável causa.
8. **Acompanhe e corrija o rumo.** Equipe sem supervisão por muito tempo desperdiça esforço.
9. **O líder espera.** Se ele começar a implementar sozinho: "Espere seus colegas terminarem
   as tarefas antes de continuar".
10. **Pré-aprove o que for comum** nas permissões antes de criar a equipe, ou o líder vira um
    balcão de aprovações.
11. **Defina papéis reutilizáveis** como subagentes em `.claude/agents/` e cite o tipo na
    criação ("crie um colega do tipo `revisor-seguranca`").

### Definição de subagente usada como colega

| Campo | Efeito no colega |
|---|---|
| `tools` | Limita as ferramentas. Em in-process, `SendMessage` e as Task tools são somadas sempre. |
| `model` | Usado se o prompt não citar modelo. |
| `disallowedTools` | (in-process) Remove ferramentas, exceto `SendMessage`/Task tools. |
| `effort`, `autoCompactWindow` | (in-process) Aplicados. |
| Corpo do arquivo | In-process: somado ao system prompt. Painel dividido: substitui o system prompt. |
| `skills` | **Ignorado.** O colega carrega as skills do projeto e do usuário. |
| `mcpServers` | Só em painel dividido. In-process usa os MCP do projeto/usuário. |

Definição em `.claude/agents/` só é reaplicada a um colega trazido de volta se a pasta do
arquivo for confiável (trust); a pasta-pai não basta.

---

## 5. Neste repositório: regras que todo prompt de colega precisa carregar

O colega lê o `CLAUDE.md`, mas o prompt deve **reforçar** o que custa caro errar:

- **Sem commit nem push**, a menos que eu (líder) tenha recebido o pedido. Merge nunca.
- **Dono de arquivos:** liste no prompt os caminhos que o colega pode editar. Mapa natural:

  | Colega | Arquivos |
  |---|---|
  | `dominio` | `backend/src/domain/` + testes correspondentes em `backend/src/tests/` |
  | `infra` | `backend/src/infrastructure/` (+ migração nova em `db/migrations/versions/`) |
  | `api` | `backend/apps/api/` + tabela de endpoints do `README.md` |
  | `worker` | `backend/apps/worker/` (tasks, `task_routes`, `include=`) |
  | `frontend` | `frontend/src/` + testes vitest |
  | `docs` | `README.md`, `CLAUDE.md`, `docs/` |

  Arquivo compartilhado (`config.py`, `README.md`, `CLAUDE.md`, `pyproject.toml`) tem **um**
  dono; os outros mandam mensagem pedindo a alteração.
- **Regra de dependência:** `domain/` não importa pymongo, httpx, celery nem fastapi.
- **Verificação no container de dev** (seção "Comandos" do `CLAUDE.md`), com a saída no
  transcript. Quem mexer em README, compose ou Dockerfile roda montando o repositório inteiro.
  Para não disputarem o mesmo container ao mesmo tempo, **a verificação final é do líder**.
- **Nunca** renove o token do ML à mão nem copie `ml_oauth` (refresh token de uso único,
  armadilha 1). Nada de `docker compose up -d --build` (armadilha 9).
- **Nada de segredo** em prompt, mensagem, log ou arquivo: o repositório é público. IDs do
  Notion também não entram em `docs/`.
- **Notion:** só o líder mexe no card, e uma `TIE-*` por vez. Colega não muda status.
- **Mudou o motor de score?** Backtest antes e depois. Mudou o prompt do LLM? Sobe
  `PROMPT_VERSION`. Default de peso? Atualiza os valores dourados de `test_scoring.py`.

---

## 6. Modelo de prompt de criação

```text
Crie uma equipe de N colegas para <objetivo>. Nomes: <a>, <b>, <c>. Modelo: <opus|sonnet>.

Contexto comum: <o que já se sabe, o que foi descartado, links/arquivos>.
Regras: siga o CLAUDE.md; não faça commit; não edite fora dos seus arquivos; peça por
mensagem ao dono quando precisar mudar um arquivo dele.

- <a>: <tarefa>. Arquivos: <caminhos>. Entrega: <formato>.
- <b>: <tarefa>. Arquivos: <caminhos>. Entrega: <formato>.
- <c>: <tarefa>. Arquivos: <caminhos>. Entrega: <formato>.

Quando todos terminarem, eu (líder) rodo pytest/ruff/black no container de dev e sintetizo.
```

---

## 7. Receitas

### 7.1 Revisão de PR em paralelo

```text
Crie três colegas para revisar o PR #NN:
- revisor-seguranca: vazamento de credencial (URL, log, traceback), validação de entrada,
  X-API-Key e rate limit nas rotas novas.
- revisor-motor: efeito no score, regra de dependência do domain, PROMPT_VERSION,
  DIMENSOES_POR_FONTE e valores dourados.
- revisor-testes: cobertura (piso do pyproject), test_docs, test_imports_smoke,
  test_worker_registro.
Cada um só lê e reporta achados com severidade e arquivo:linha. Não editem nada.
```

### 7.2 Hipóteses concorrentes

```text
O ranking do ML parou de mudar entre ciclos. Crie 4 colegas, cada um com uma hipótese
(coleta parada/circuito aberto, cache do LLM, corte de TREND_MAX_PRODUCTS, dedupe de insight).
Eles devem trocar mensagens tentando derrubar a hipótese um do outro, como num debate
científico, só lendo código e banco. Me tragam o consenso e a evidência.
```

### 7.3 Feature entre camadas

```text
Crie três colegas para <feature>: dominio (regra + testes em src/domain e src/tests),
api (rota em apps/api/routes.py + tabela do README) e frontend (frontend/src + vitest).
A api depende do contrato do dominio: dominio publica a assinatura por mensagem antes de
implementar. Ninguém edita arquivo do outro.
```

---

## 8. Problemas comuns

| Sintoma | O que fazer |
|---|---|
| Colegas não aparecem | Veja o painel abaixo do prompt (↑/↓). A tarefa pode não ter justificado equipe; peça explicitamente "uma equipe de agentes". Painel dividido: `which tmux`. |
| Linha do colega sumiu | Ocioso fica escondido, não parado. Mande mensagem pelo nome. |
| Veio subagente em vez de equipe | O painel mostra os dois. Peça de novo, explicitamente equipe. |
| Virou equipe sem eu pedir | Subagente nomeado vira colega com a variável ligada. Desligue com `=0`. |
| Excesso de pedidos de permissão | Pré-aprove as operações comuns antes de criar a equipe. |
| Colega parou num erro | Abra a transcrição dele e dê instruções, ou crie um substituto. |
| Líder declarou fim cedo | "Continue: ainda há tarefas abertas." |
| Tarefa travada em `in progress` | Confira se o trabalho está feito e atualize, ou peça ao líder que cobre o colega. |
| tmux órfão | `tmux ls` e `tmux kill-session -t <nome>`. |

## 9. Limitações

- **`/resume` e `/rewind` não restauram colegas in-process.** O líder pode tentar falar com
  quem não existe mais: peça para criar colegas novos.
- **Uma equipe por sessão**, sem compartilhar entre sessões. **Sem equipes aninhadas:** colega
  não cria colegas.
- **Colega in-process não roda subagente em background** (`background: true` dá erro).
- **O líder é fixo**, sem transferência.
- **Permissão só se ajusta depois de criar** o colega.
- **Encerramento é lento:** o colega termina a chamada em curso antes.
- **Cache:** a requisição do colega in-process tem cache de 5 min; `subagentPromptCacheTtl: "1h"`
  estende, com escrita mais cara.

## 10. Registro de aprendizados

Depois de cada equipe usada aqui, acrescente uma linha: data, tarefa, composição, o que
funcionou e o que não funcionou. É isso que torna as próximas equipes melhores.

| Data | Tarefa | Equipe | Aprendizado |
|---|---|---|---|
| 2026-10-22 | Teste de fumaça: rotas da API × tabela do README | 2 colegas sonnet, só leitura (`leitor-api` → `leitor-docs`) | Funcionou: mensagem direta entre colegas, sem passar pelo líder; resultado (14 rotas, nenhuma divergência) bate com o grep do líder e com o `test_docs.py`. O colega que espera mensagem fica ocioso e acorda sozinho quando ela chega: no prompt, diga "use a lista recebida, não extraia você mesmo", senão ele faz o trabalho do outro e o teste não prova nada. Edição do `.claude/settings*.json` pelo líder é bloqueada no modo automático (auto-modificação): permissão é o usuário quem ajusta. |
