# De puxadinho a runtime: uma ideia sobre portar isso pro Agno

> [!NOTE]
> Antes de mais nada: isso não é crítica nem "olha como vocês fazem errado". É só o que a gente foi aprendendo aqui na BF Labs, testando bastante coisa em produção — com números e incidentes reais (sem nome de cliente, claro). Compartilho mais como sugestão do que como receita — pega o que fizer sentido pro que você está construindo.

---

## 1. Como a BF Labs monta esse tipo de sistema hoje

Nosso stack pra agente de mensageria (WhatsApp, Instagram, Facebook) costuma seguir o mesmo esqueleto:

| Camada | O que usamos |
|---|---|
| **Linguagem / API** | Python + FastAPI, um serviço por agente (systemd + porta própria) |
| **Runtime de agente** | [Agno](https://github.com/agno-agi/agno) — `Agent`, tools decoradas, sessão, memória |
| **Banco** | Postgres (`AsyncPostgresDb` do próprio Agno), schema fixado por serviço |
| **Canal WhatsApp** | uazapi (QR/grupos) ou WABA (API oficial Meta), dependendo do cliente |
| **Canal social** | Zernio, pra Instagram/Facebook (comentários + DMs) |
| **Roteamento de LLM** | OmniRoute — um gateway único na frente dos providers |
| **Exposição pública** | Cloudflare Tunnel, um subdomínio por agente |

> [!TIP]
> **Recursos do Agno**
> - [Site / GitHub do Agno](https://github.com/agno-agi/agno) — o framework em si, código aberto.
> - [Documentação oficial](https://docs.agno.com) — guias, referência de API, exemplos.
> - [Skill do Agno no skills.sh](https://skills.sh/agno-agi/agno) — pacotes prontos (git-workflow, code-review, system-info) pra instalar direto no agente de código que você usa (`npx skills add agno-agi/agno`).

Cada agente novo nasce clonando um blueprint, ganha uma porta, um `.env`, e sobe como serviço isolado. Isso já ajuda bastante com o "puxadinho" clássico — não é um monólito costurando 10 clientes; cada agente é um processo próprio, com seu próprio banco e sua própria sessão.

Visualmente, uma mensagem passa mais ou menos por isso até virar resposta:

```mermaid
graph LR
  A["Webhook (uazapi / WABA / Zernio)"] --> B["Dedup por ID"]
  B --> C["Debounce buffer (~8s)"]
  C --> D["Lock por session_id"]
  D --> E["Agent (Agno)"]
  E --> F["OmniRoute"]
  F --> G["Provider (DeepSeek, Gemini, etc.)"]
  G --> H["Guarda de conteúdo"]
  H --> I["Envio ao usuário"]
```

O ponto que ainda dá trabalho não é essa camada de infraestrutura. É a caixinha **"Guarda de conteúdo"** ali no fim — a parte que decide o que realmente sai pro usuário. É esse pedaço que fica mais interessante de olhar, e é o assunto do resto deste texto.

---

## 2. O stack do Hermes (nosso agente interno)

Antes de qualquer comparação: o Hermes é ótimo, a gente usa ele pra muita coisa aqui dentro, e não é uma alternativa ao Agno — são duas camadas diferentes, cada uma boa no que faz.

Temos alguns perfis de Hermes rodando, cada um voltado pra um tipo de trabalho:

| Perfil | O que faz |
|---|---|
| **Infra / DevOps** | Sobe agente novo, mexe em túnel Cloudflare, gerencia serviço, faz deploy |
| **Git / repo ops** | Cuida de commit, branch, PR, revisão entre repositórios |
| **Provisionamento de cliente** | Faz o scaffolding de agente novo (clona blueprint, configura `.env`, registra porta) |
| **Curadoria de conhecimento** | Organiza documentação, mantém a base de memória (second brain) atualizada |
| **Pesquisa / operações de cliente** | Roda pesquisa de mercado, gerencia campanha, opera CRM |

Todos esses perfis têm um ar de família: são bastante **supervisores e middlemen** — orquestram, delegam, decidem "qual ferramenta chamar agora", tocam processo de ponta a ponta. E fazem isso bem — o forte deles é lidar com ambiguidade, coordenar etapa, pedir confirmação quando faz sentido.

```mermaid
graph TD
  H["Hermes — perfis supervisores / middlemen<br/>infra, git ops, provisionamento, curadoria, pesquisa"]
  H --> O["Camada de execução determinística da conversa"]
  O --> AG["Agno — Agent, Team, output_schema, guardrails"]
```

Onde eles entram menos é como motor que gera, de forma determinística e testável, a resposta que um cliente final recebe automaticamente, sem ninguém supervisionando aquele turno específico. Pra essa parte — o motor de execução da conversa, mais do que a supervisão do processo — testamos algumas opções ao longo do tempo, e o Agno foi o que, na nossa experiência, se mostrou o mais determinístico, com menos erro, e com a biblioteca mais completa entre o que já rodamos em produção. Foi meio que por isso que ele acabou virando a peça padrão por baixo de todo agente de cliente, enquanto o Hermes fica mais na camada de operação em volta.

> [!TIP]
> Tem umas ideias na arquitetura do Hermes que acho que valem a pena mesmo fora do nosso contexto:
> - **Gateway + Dashboard** — um serviço central recebendo os pedidos, em vez de cada ferramenta falando direto com o mundo. Sai auditoria de graça, porque tudo passa por um ponto só que loga.
> - **OmniRoute como camada de roteamento de modelo** — nenhuma chamada de LLM sai direto pra um provider específico; passa por um proxy que decide qual usar, cuida de fallback, cota e custo.
> - **ClawMem como memória de longo prazo** — diferente da memória de conversa (seção 3): é uma vault que indexa e recupera contexto — decisão passada, preferência, incidente já resolvido — por busca semântica, pra não ficar repetindo o mesmo erro.

O motivo de trazer isso aqui não é "usa nosso agente interno" — é mais o padrão em si: **um gateway único + um roteador de modelo único + uma camada de memória única**, como peças de infraestrutura reaproveitadas, em vez de código específico por projeto.

---

## 3. Memória: duas camadas diferentes, dois problemas diferentes

Uma coisa que ajuda bastante é não tratar "memória" como uma coisa só — na prática dá pra separar em duas camadas com propósitos bem diferentes.

<Tabs>
  <Tab label="Sessão / conversa">

É o "o que esse contato específico já disse, já comprou, em que etapa do funil está". No nosso stack isso fica quase todo a cargo do próprio Agno:

- `db=AsyncPostgresDb(...)` persiste o histórico automaticamente — não precisa escrever código de "salvar mensagem no banco".
- `add_history_to_context=True` + `num_history_runs` decide quanto do histórico entra no próximo prompt, sem montar isso na mão a cada turno.
- `agno.learn` (LearningMachine) guarda preferência e fato sobre aquele usuário (nome preferido, produto de interesse, etapa do funil) e resgata isso entre sessões diferentes.
- `session_state` guarda o estado de curto prazo da conversa (ex.: "estou no meio de uma coleta de dado, esperando o CPF que pedi há 2 mensagens").

  </Tab>
  <Tab label="Conhecimento (RAG)">

Diferente de "o que esse lead me disse" — é "qual é a regra, o preço, o protocolo, a FAQ". Aqui entra RAG de verdade: Qdrant como vector store, uma tool de busca semântica que o agente chama quando a pergunta foge do que já está no prompt.

Uma coisa que aprendemos com a prática: informação sensível (política de preço, tratamento de objeção, resposta a pergunta delicada) a gente prefere colocar direto no prompt, não via RAG — RAG funciona bem pra FAQ institucional, mas regra de negócio crítica fica mais segura garantida no prompt do que "recuperável, se o embedding acertar".

  </Tab>
</Tabs>

Vale ficar de olho nessa distinção porque é uma fonte comum de confusão: tentar resolver "o agente não lembra que eu já falei isso" com uma solução de RAG, quando o problema costuma ser configuração de sessão/histórico.

---

## 4. Providers: um ponto de entrada único

Um hábito que ajudou bastante aqui: nenhum agente fala direto com a API de um provider de LLM — tudo passa pelo OmniRoute, nosso gateway de roteamento. Não é purismo, é mais prático mesmo:

- Troca de modelo por cliente (ex.: um usa DeepSeek, outro usa Gemini) vira uma variável de ambiente, não um `if` espalhado pelo código.
- Fallback de modelo (o principal falhou → cai pro backup) é uma política central, testada uma vez, herdada por todo agente.
- Custo e cota ficam visíveis num lugar só, porque toda chamada passa pelo mesmo proxy.
- Quando um provider começa a se comportar estranho (timeout, resposta esquisita, alucinação de um modelo específico) dá pra perceber olhando um painel só, em vez de caçar log em vários serviços.

O mesmo raciocínio vale pros canais de mensageria — WhatsApp via uazapi ou WABA, social via Zernio: cada canal fica numa camada de adapter isolada, e o agente nunca precisa saber qual provider está por trás, só "manda essa mensagem pra esse contato".

---

## 5. Fila e buffer: mensagem em rajada e concorrência

Duas coisas ajudam bastante com "usuário manda 4 mensagens seguidas e o bot responde 4 vezes, cada uma sem o contexto da anterior":

**Debounce buffer** — cada mensagem nova reseta um timer (normalmente uns 8 segundos); só quando o silêncio dura o suficiente é que o texto acumulado vai pro agente de uma vez. Lógica pura, determinística, fácil de testar isolada (o tempo é injetado, não é `sleep` real).

**Pool de sessão com lock por contato** — cada conversa tem um semáforo/lock próprio (`session_id`). Isso deixa várias conversas diferentes processarem em paralelo, mas garante que a *mesma* conversa nunca processa duas mensagens ao mesmo tempo.

Fora isso, tem uma camada simples de deduplicação de webhook (o mesmo provedor às vezes reenvia o mesmo evento) — checa por ID de mensagem antes de entrar na fila.

Nada disso é exclusivo do Agno, mas ele acaba dando um lugar natural pra plugar cada peça — o objeto de sessão já existe, então não precisa inventar onde guardar o lock.

---

## 6. Onde a coisa costuma apertar mais: a guarda de conteúdo

Tudo que descrevi até aqui (infra, providers, fila, memória) é a parte que, uma vez resolvida, fica resolvida — reusa em todo agente novo. A parte que continua dando trabalho, e que nenhum framework resolve sozinho, é: como garantir que o texto que sai pro usuário final é o que a gente quer que saia, e não o modelo alucinando, vazando raciocínio interno, ou repetindo um erro que já apareceu antes?

Aqui vou contar algumas coisas que aconteceram de verdade com a gente usando Agno em produção — não é hipótese, foi aprendendo na marra mesmo.

> [!WARNING]
> **Caso A — um SDR de WhatsApp num setor mais sensível**
>
> Um agente nosso de triagem/vendas via WhatsApp, num nicho onde a resposta errada pesa mais (saúde). Já roda todo em Agno (`Agent` + Postgres + tools decoradas). Mesmo assim, por umas duas semanas, o modelo por trás dele começou a devolver, de vez em quando, uma string de recusa em inglês como se fosse resposta normal — sem erro nenhum sinalizado pelo roteador — mesmo pra mensagens completamente inofensivas do lead. Isso chegou a usuários reais 9 vezes nesse período, incluindo 4 vezes em 16 minutos pro mesmo contato, porque uma vez que o texto quebrado entra no histórico da sessão ele tende a se repetir no turno seguinte.
>
> A correção que existe hoje é um arquivo Python escrito à mão: uma lista de frases conhecidas + um dicionário de palavras comuns em português, com a regra "resposta longa sem nenhuma palavra em PT-BR é suspeita, bloqueia". Funciona, está em produção — mas é uma correção pontual, vivendo dentro de um repo só.

> [!WARNING]
> **Caso B — um agente de resposta pública em rede social**
>
> Outro agente nosso, também em Agno, responde comentários e DMs em Instagram/Facebook. Bug diferente, mas parecido no fundo: faltou um filtro pra "essa é a minha própria resposta voltando como se fosse um comentário novo". As respostas do agente reentravam no webhook, e a cada volta o modelo — agora "respondendo" ao próprio texto de antes — passava a narrar o próprio raciocínio em vez de escrever uma resposta de verdade (algo tipo "Sem ação necessária — não é uma pessoa buscando ajuda, é registro de sistema"). Isso foi publicado 21 vezes, publicamente, no mesmo post, até alguém notar o loop e parar o serviço.
>
> A correção foi, de novo, uma lista de frases-heurística escrita à mão pra pegar "o modelo narrando a própria decisão" — o comentário no próprio código até reconhece que é basicamente o mesmo padrão do guard do caso A, feito do zero, num repo diferente.

> [!CAUTION]
> **Caso C — um clone mais recente**
>
> Um terceiro agente foi construído copiando a estrutura do Caso A — mesmo jeito de montar o `Agent`, mesmo padrão de prompt. E o arquivo de guarda do Caso A foi copiado junto pra dentro desse repo novo.
>
> Esse é talvez o ponto mais interessante dos três juntos: a correção do Caso A nunca virou um componente reutilizável — virou um arquivo pra copiar e colar toda vez que nasce um agente novo. Se um dia o padrão de vazamento mudar, alguém vai precisar lembrar de atualizar isso em mais de um lugar.

> [!CAUTION]
> **Caso D — o agente com mais volume**
>
> Nosso agente com mais usuários reais tem uma guarda ainda mais frágil pra conteúdo crítico: um teste automatizado que não valida uma função, valida se um trecho de texto específico ainda está presente dentro do prompt livre — tipo conferir se a palavra "LGPD" ainda aparece em algum lugar do system prompt. Isso ajuda a garantir que ninguém apagou sem querer uma instrução crítica, mas não garante que o modelo vai obedecer aquela instrução — é meio que confiar que uma frase solta num parágrafo grande vai ser seguida à risca.

Juntando os quatro num resumo rápido:

```html preview
<div style="font-family:system-ui,sans-serif;padding:20px">
  <div id="cards" style="display:flex;gap:14px;flex-wrap:wrap"></div>
  <script>
    var stats = [
      ['Caso A — envios reais com texto quebrado', '9', 'em 2 semanas', 'var(--chart-5)'],
      ['Caso B — publicações públicas indevidas', '21', 'no mesmo post', 'var(--chart-5)'],
      ['Caso C — repositórios com o mesmo guard copiado', '3+', 'e contando', 'var(--chart-3)'],
      ['Achado geral — agentes usando agno.guardrails', '0', 'de 4', 'var(--chart-4)']
    ];
    document.getElementById('cards').innerHTML = stats.map(function (s) {
      return '<div style="flex:1;min-width:170px;padding:16px;background:var(--card);' +
        'color:var(--card-foreground);border:1px solid var(--border);' +
        'border-radius:var(--radius)">' +
        '<div style="font-size:12.5px;color:var(--muted-foreground);line-height:1.3">' + s[0] + '</div>' +
        '<div style="font-size:28px;font-weight:700;margin-top:6px">' + s[1] + '</div>' +
        '<div style="font-size:12px;font-weight:600;margin-top:4px;color:' + s[3] + '">' +
        s[2] + '</div>' +
        '</div>';
    }).join('');
  </script>
</div>
```

---

## 7. O que achei mais interessante nisso tudo

Olhando os quatro casos juntos: em nenhum deles falta o Agno — os quatro já rodam sobre `Agent` / Postgres / `agno.learn`. O que ainda não usamos direito são as peças do próprio framework feitas exatamente pra esse tipo de problema, em vez de reescrever a mesma ideia à mão:

- **`output_schema`** — faz o modelo devolver dado validado (Pydantic), não texto livre auditado só depois por regex. Uma string de recusa em inglês provavelmente nem passaria como campo válido de um schema esperado.
- **`agno.guardrails`** — o framework já vem com um módulo pronto: detecção de PII, de prompt injection, moderação de conteúdo, e uma classe-base (`BaseGuardrail`) pra escrever a própria regra como objeto plugável no Agent, em vez de arquivo solto que cada repo precisa lembrar de importar.
- **`Team`** — um segundo agente cuja única função é conferir a resposta do primeiro antes dela sair, testável isolado, não uma lista de frases perdida no meio do código.

```mermaid
graph TD
  subgraph Hoje["Hoje — guarda duplicada"]
    A1["Agente A<br/>response_guard.py"]
    B1["Agente B<br/>comment_guard.py"]
    C1["Agente C<br/>response_guard.py copiado"]
    D1["Agente D<br/>assert em texto de prompt"]
  end
  subgraph Proposta["Proposta — guarda compartilhada"]
    S["shared/guardrails<br/>BaseGuardrail"]
    S --> A2["Agente A"]
    S --> B2["Agente B"]
    S --> C2["Agente C"]
    S --> D2["Agente D"]
  end
```

Nenhum dos nossos quatro agentes usa essas peças hoje — cada um acabou escrevendo (ou copiando) sua própria versão do mesmo problema. Isso não é falha do Agno, é mais a gente ainda não ter padronizado o uso dessas partes específicas dele.

---

## 8. Resumindo pro que você está construindo

Algumas coisas que eu levaria dessa experiência, mais como sugestão do que regra:

- Adotar um framework de agentes já resolve boa parte da metade "chata" do problema — fila, sessão, histórico, roteamento de modelo, ferramentas por intenção. O Agno faz isso nativamente, e isso sozinho já ajuda bastante com o puxadinho clássico.
- Mas ele protege mais contra alucinação quando dá pra usar as peças estruturais dele de propósito — `output_schema` em vez de texto livre auditado depois, guardrails plugados no Agent em vez de reimplementados, um `Team` verificador pra decisão mais crítica em vez de heurística solta no meio do fluxo.
- E talvez o ponto mais prático: quando aparece uma correção pra um incidente, vale a pena tentar deixar ela num lugar compartilhado, reusável entre agentes, em vez de um arquivo que alguém vai acabar copiando pro próximo cliente.

> [!IMPORTANT]
> No fim, a sequência que você já tem — validar antes, controlar o que a IA pode fazer, conferir depois, auditar tudo — parece certa como desenho. O que muda ao portar pro Agno não é "o modelo passa a alucinar menos por mágica", é mais trocar um pouco do cimento feito à mão por peças com contrato (`Agent`, `Team`, `output_schema`, `agno.guardrails`) que já foram testadas por bastante gente, e que dá pra reaproveitar entre agentes. E o aprendizado extra que a gente teve aqui: só trocar de framework não resolve sozinho — se a guarda de segurança continuar sendo lista de frases coladas em cada repo, acaba sendo o mesmo puxadinho, só que em cima de uma lib mais bonita.

---

## 9. Por que eu bato tanto nessa tecla

> [!TIP]
> Uma coisa pessoal, já que a gente tá conversando: eu defendo o Agno com bastante convicção porque vejo pouca gente falando sobre ele — é meio underrated pro tanto que entrega. Uso n8n, automação e construção de fluxo desde 2019 (comecei lá atrás com n8n 0.8, era tudo mato e hoje nem uso mais rsrs), e sinceramente o Agno foi uma das coisas que mais mudou a forma como eu penso construção de agente de IA nos últimos tempos. Não é só "mais uma lib" — é o tipo de ferramenta que faz eu repensar como estruturar o problema inteiro. Por isso insisto tanto nisso com você: não é modinha, é o que realmente tem funcionado aqui.
