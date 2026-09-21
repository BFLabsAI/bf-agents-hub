# Ítalo — Agente de IA iTarget

Concierge de eventos da plataforma iTarget operando via WhatsApp Business. Guia associados do início ao fim: descoberta de eventos → inscrição → categoria profissional → pagamento (PIX, boleto, cartão).

## Stack

| Componente | Tecnologia |
|---|---|
| Framework de agente | **Agno** |
| LLM (produção) | **x-ai/grok-4.1-fast** via OpenRouter |
| LLM padrão (fallback) | `anthropic/claude-3-haiku` (env `OPENROUTER_MODEL_ID`) |
| Gateway LLM | OpenRouter (`https://openrouter.ai/api/v1`) — OpenAI-compatible |
| Storage de sessão | SQLite via Agno (`tmp/italo.db`) |
| Web framework | FastAPI + Uvicorn |
| Protocolo de mensagens | Meta WhatsApp Business API (WABA) |
| HTTP client | httpx (assíncrono) |

## Estrutura de arquivos

```
main/
├── app/
│   ├── agent_factory.py       # Cria instância do Agent Agno com todas as tools
│   ├── config.py              # Variáveis de ambiente (.env)
│   ├── context.py             # SessionContext — wrapper tipado sobre session_state
│   ├── itarget_client.py      # Cliente HTTP da API iTarget
│   ├── llm_factory.py         # build_model() → OpenAIChat apontando para OpenRouter
│   ├── log_store.py           # LogStore in-memory + SQLite de transações
│   ├── payment_webhook_log.py # Tabela de webhooks de pagamento recebidos
│   ├── transaction_log.py     # Estruturas de dados para log
│   ├── waba_client.py         # Envia mensagens (texto, carousel, PIX, boleto) via WABA
│   ├── whatsapp_api.py        # FastAPI app — entry point de todos os webhooks
│   ├── whatsapp_bridge.py     # Bridge WABA ↔ agente + pool de sessões (AgentRuntime)
│   └── admin_router.py        # Dashboard admin REST + SSE stream
├── tools/
│   ├── identity.py            # identify_by_phone, identify_by_document
│   ├── events.py              # list_events, event_detail
│   ├── category.py            # get_cost_center_person, list_categories, register_category
│   ├── subscription.py        # check_existing_subscription, list_payment_plans,
│   │                          # create_subscription, my_subscriptions,
│   │                          # cancel_subscription, select_pending_payments
│   └── payment.py             # list_payment_methods, process_pix_payment,
│                              # process_bank_payment, print_bank_payment
├── prompts/
│   └── italo.py               # build_system_prompt() → description + instructions (120+ linhas)
├── static/
│   └── admin.html             # Dashboard web de sessões/transações em tempo real
├── main.py                    # CLI interativo para testes locais
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## Como o agente funciona

### Criação (`app/agent_factory.py`)

```python
Agent(
    name="Ítalo",
    id="italo-itarget-agent",
    session_id=session_id,           # "italo-wa-{numero}" ou "italo-cli-dev"
    model=build_model(),             # OpenRouter → grok-4.1-fast
    tools=all_tools,                 # 13 tools em 5 grupos
    session_state=state,             # dict com estado do usuário
    db=SqliteDb(db_file=SESSION_DB_PATH),
    add_history_to_context=True,
    num_history_runs=10,
    description=prompt["description"],
    instructions=prompt["instructions"],
    markdown=False,
)
```

Cada usuário WhatsApp tem sua própria sessão (`session_id = "italo-wa-{numero}"`). O Agno persiste `session_state` automaticamente no SQLite após cada run.

### SessionContext (`app/context.py`)

Wrapper tipado sobre o dicionário `agent.session_state`. Todas as mutações feitas via setters propagam para o dict que o Agno persiste no SQLite — nenhum código de persistência manual necessário.

**Campos do state:**
- Identidade: `from_number`, `person_id`, `first_name`, `access_token`, `person_hash_link`
- Contexto de evento: `activity_schedule_id`, `cost_center_id`, `event_title`, `event_amount`
- Contexto de pagamento: `account_receive_ids` (lista), `payment_plan_id`, `print_token`
- Anuidades: `annuity_subscription_ids`, `pending_membership_details`
- Caches: `available_events`, `available_categories`, `available_payment_methods`

### Pool de agentes (`app/whatsapp_bridge.py`)

`AgentRuntime` mantém um dict em memória `{session_id → Agent}` com Lock para thread safety. Ao receber uma mensagem WhatsApp, o bridge recupera ou cria o agente para aquela sessão e chama `agent.arun(msg.text, session_state=agent.session_state)`.

## Fluxo de produção

```
Meta WABA
  → POST /webhooks/waba
      → valida assinatura HMAC-SHA256 (X-Hub-Signature-256)
      → parse_waba_payload() → IncomingWABAMessage
      → asyncio.create_task(_handle_message(msg))   ← responde Meta imediatamente
      → bridge.generate_reply(msg)
          → AgentRuntime.get_agent(session_id)
          → agent.arun(text)
              → tools chamam iTarget REST API
              → tools podem enviar mensagens ricas diretamente via waba_client
          → retorna texto final
      → waba.send_text(to, reply)
```

**Conversão de interações:** Cliques em botões/listas do WhatsApp (`button_reply`, `list_reply`) são convertidos para texto natural antes de chegar ao agente. IDs `info_{id}` → `"Quero mais informações sobre o evento {id}"`, `sub_{id}` → `"Quero me inscrever no evento {id}"`.

**Comando `/flush`:** Apaga a sessão do SQLite e do pool in-memory. Útil para reiniciar o contexto em debug.

## Tools — grupos e funções

### Identity (`tools/identity.py`)
| Tool | Descrição |
|---|---|
| `identify_by_phone()` | Autentica pelo número WhatsApp. SEMPRE chamada como primeira ação. Se `not_found`, pede CPF. Normaliza número BR (insere 9º dígito se necessário). |
| `identify_by_document(document)` | Autentica por CPF/email. Limpa pontuação antes de enviar para API. |

Ambas armazenam `person_id`, `access_token`, `first_name`, `person_hash_link` no `SessionContext`.

### Events (`tools/events.py`)
| Tool | Descrição |
|---|---|
| `list_events()` | Lista eventos disponíveis. Se `waba_client` disponível, envia carousel interativo (≥2 eventos) ou list message (1 evento) diretamente para o usuário via WhatsApp. Exclui categorias `ASSOCIAÇÃO` e imagens S3 assinadas (quebram o carousel). |
| `event_detail(activity_schedule_id)` | Detalhes de um evento específico. Armazena `activity_schedule_id`, `cost_center_id`, `event_title` no contexto. |

### Category (`tools/category.py`)
| Tool | Descrição |
|---|---|
| `get_cost_center_person()` | Verifica se usuário tem categoria profissional registrada no centro de custo do evento. |
| `list_categories()` | Lista categorias disponíveis para o centro de custo atual. |
| `register_category(category_number / category_description)` | Registra o usuário em uma categoria. Suporta fuzzy match por descrição. |

### Subscription (`tools/subscription.py`)
| Tool | Descrição |
|---|---|
| `check_existing_subscription()` | Verifica inscrição existente no evento atual. Se inscrito com pendência, armazena `account_receive_ids`. |
| `list_payment_plans()` | Retorna preço do evento para a categoria do usuário. Pode retornar `pendingMembershipFee` (opção com/sem anuidade). Armazena `payment_plan_id`. |
| `create_subscription(include_annuity)` | Cria inscrição. Requer `payment_plan_id` no contexto. Pode incluir anuidade pendente. |
| `my_subscriptions()` | Lista todas as inscrições do usuário (pagas, pendentes, canceladas). |
| `cancel_subscription(subscription_ids)` | Cancela inscrições por ID. |
| `select_pending_payments(activity_schedule_ids)` | Prepara pagamento de múltiplas inscrições pendentes — carrega `account_receive_ids` para todas. |

### Payment (`tools/payment.py`)
| Tool | Descrição |
|---|---|
| `list_payment_methods()` | Descobre métodos disponíveis (PIX, Boleto, Cartão) para os `account_receive_ids` atuais. Retorna `availableLabels` e `checkoutUrl`. |
| `process_pix_payment()` | Gera PIX e envia mensagem interativa WhatsApp (QR code + copia-e-cola) diretamente ao usuário. `sent_via_whatsapp=true` → agente NÃO repete o código em texto. |
| `process_bank_payment()` | Gera boleto, envia PDF + linha digitável como mensagens interativas. `sent_via_whatsapp=true` → agente NÃO repete códigos em texto. Extrai PDF de páginas HTML de gateways (Vindi/Yapay). |
| `print_bank_payment()` | Obtém URL do PDF do boleto pelo `print_token`. Só chamar se `process_bank_payment` não enviou via WhatsApp. |

## Fluxo de negócio (state machine implícita)

```
identify_by_phone
  ↓ (not_found → pede CPF → identify_by_document)
list_events → event_detail
  ↓
check_existing_subscription
  ├─ already_subscribed → list_payment_methods
  └─ not_subscribed → get_cost_center_person
       ├─ registered → list_payment_plans
       └─ not_registered → list_categories → (usuário escolhe) → register_category
                                                                     ↓
                                                              list_payment_plans
  ↓ (usuário confirma valor)
create_subscription
  ↓
list_payment_methods
  ├─ PIX    → process_pix_payment
  ├─ Boleto → process_bank_payment
  └─ Cartão → envia checkoutUrl (link autenticado via hashLink + base64)
```

**Pagamentos múltiplos:** `select_pending_payments([id1, id2])` → `list_payment_methods` → método único para tudo.

## Webhooks externos

### WABA (Meta WhatsApp)
- `GET /webhooks/waba` — verificação de challenge (Meta)
- `POST /webhooks/waba` — mensagens recebidas (autenticado por HMAC-SHA256)

### Confirmação de pagamento
- `POST /webhooks/payment-confirmed` — recebe confirmação externa de pagamento e envia mensagem WhatsApp ao usuário
- Autenticado pelo header `X-Webhook-Secret`
- Campos: `phone`, `person_name`, `event_name`, `amount`, `payment_method`, `due_date`, `installments`

### Admin
- `GET /admin` — dashboard HTML
- `GET /admin/sessions` — lista sessões ativas
- `GET /admin/sessions/{id}/messages` — conversa completa
- `GET /admin/transactions` — log de todas as trocas de mensagens
- `GET /admin/payment-webhooks` — log de confirmações de pagamento
- `GET /admin/stream` — SSE stream em tempo real

## Variáveis de ambiente

```bash
# iTarget API
ITARGET_API_BASE_URL=https://demo.api.itarget.com.br
ITARGET_CLIENT=demo
ITARGET_ENV=          # vazio para prod, ".stg" para staging

# LLM via OpenRouter
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL_ID=x-ai/grok-4-1-fast   # produção

# Sessão
SESSION_DB_PATH=tmp/italo.db

# Meta WABA
WABA_PHONE_NUMBER_ID=
WABA_ACCESS_TOKEN=
WABA_WEBHOOK_VERIFY_TOKEN=
WABA_APP_SECRET=

# PIX
PIX_MERCHANT_NAME=iTarget
PIX_KEY=              # chave EVP UUID
PIX_KEY_TYPE=EVP

# Webhook de pagamento
PAYMENT_WEBHOOK_SECRET=   # deixar vazio desabilita autenticação (só dev)
```

## Como rodar

```bash
# Desenvolvimento (CLI interativo)
python main.py

# Produção
uvicorn app.whatsapp_api:app --host 0.0.0.0 --port 8000

# Docker
docker-compose up
```

## Observações importantes

- **Nunca confiar no cache do contexto para dados de negócio.** As tools sempre buscam dados frescos da API. Regra do prompt: nunca responder sobre status de inscrições/pagamentos da memória da conversa.
- **`nextActionHint`** — todas as tools retornam esse campo com instrução explícita para o LLM sobre o próximo passo. Isso compensa regressões de raciocínio do modelo e mantém o fluxo correto independente da versão do LLM.
- **Imagens S3 assinadas** — URLs com query params (`?X-Amz-*`) são inacessíveis ao CDN do WhatsApp e quebram o carousel. O `list_events` filtra essas imagens automaticamente.
- **Link de cartão** — gerado dinamicamente via `base64(account_receive_ids)` + `hashLink` do usuário. Se o usuário não tiver `hashLink`, retorna `card_only_no_link` e orienta login manual.
- **Sessão CLI** — `session_id = "italo-cli-dev"`, sem `waba_client`. Tools de pagamento e eventos caem no fallback de texto puro (sem envio WhatsApp).
