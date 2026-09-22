# BF Agents Hub — Motor Agnóstico de Agentes de IA

Repositório central de **templates de produção para agentes de WhatsApp** construídos com o framework **[Agno](https://github.com/agno-agi/agno)**, **FastAPI** e **PostgreSQL**.

Este repositório foi desenhado para ser **100% agnóstico**: o Git rastreia apenas o núcleo compartilhado (`shared/`) e os templates limpos (`templates/`). Todas as instâncias reais de clientes vivem isoladas em `clients/` (ou em diretórios locais) e são ignoradas pelo `.gitignore`.

---

## 1. Templates Disponíveis

| Template | Canal | Protocolo | Banco | Caso de Uso Principal |
|---|---|---|---|---|
| **`templates/sdr-whatsapp-uazapi/`** | WhatsApp | Não-Oficial (UazAPI) | PostgreSQL (`AsyncPostgresDb`) | Qualificação ativa (SDR), cadência de follow-ups automáticos (APScheduler), repasse para vendedores humanos e sincronização com GoHighLevel (GHL). |
| **`templates/whatsapp-oficial-waba/`** | WhatsApp | Oficial (Meta Cloud API v23.0) | PostgreSQL (`PostgresDb` + pool psycopg) | Atendimento consultivo, concierge, mensagens ricas interativas (botões, listas, carrosséis, mídias), human takeover (pausa da IA) e painel admin em tempo real. |

> 💡 **Skill Especialista WABA:** Ao desenvolver ou debugar com a API Oficial da Meta, consulte a skill **`bf-waba-expert`** (`~/.agents/skills/bf-waba-expert/` ou `skill://bf-waba-expert`). Ela documenta as regras da Graph API, validação de HMAC-SHA256, diagnóstico de falhas silenciosas de webhook e coexistência.

---

## 2. Arquitetura do Repositório

```
bf-agents-hub/
├── .gitignore                      # Protege clientes, bancos locais e segredos (.env)
├── README.md                       # Guia de arquitetura e deploy para desenvolvedores
├── AGENTS.md                       # Regras e contexto operacional para agentes de IA (Claude, Hermes, etc.)
├── PORTS.example.md                # Tabela modelo de portas e rotas
├── requirements.txt                # Dependências Python globais
│
├── shared/                         # Módulos compartilhados agnósticos
│   ├── __init__.py
│   ├── model_factory.py            # Gateway LLM (OmniRoute / OpenRouter / Anthropic)
│   ├── webhook_core.py             # Normalização e helpers para UazAPI
│   └── webhook_core_waba.py        # Normalização e helpers para Meta Cloud API v23.0
│
├── templates/                      # CÓDIGO FONTE TRACKEADO PELO GIT
│   ├── sdr-whatsapp-uazapi/        # Template SDR (UazAPI + Postgres)
│   │   ├── .env.example
│   │   ├── agent.py
│   │   ├── app.py
│   │   ├── system_prompt.py
│   │   ├── tools.py
│   │   ├── config/                 # identity, cadence, handoff, crm, channel
│   │   ├── core/                   # message_buffer, cadence_engine, lead_manager
│   │   ├── cron/                   # followup_dispatcher, cadence_scheduler
│   │   └── db/                     # schema.sql parametrizado
│   │
│   └── whatsapp-oficial-waba/      # Template Oficial (Meta Cloud API v23.0 + Postgres)
│       ├── .env.example
│       ├── main.py
│       ├── app/
│       │   ├── config.py
│       │   ├── db.py               # Pool psycopg2 central com thread safety
│       │   ├── agent_factory.py    # Agno Agent + PostgresDb singleton
│       │   ├── waba_client.py      # Cliente Meta Graph API v23.0
│       │   ├── whatsapp_bridge.py  # Pool de agentes em memória com locks por número
│       │   ├── whatsapp_api.py     # FastAPI webhook GET/POST e health
│       │   ├── media_processor.py  # Transcrição de áudio e visão multimodal
│       │   ├── message_buffer.py   # Debounce de mensagens em rajada
│       │   ├── agno_session_store.py # Leitura segura de agno_sessions
│       │   └── pause_registry.py   # Human takeover / pausa de IA
│       ├── prompts/
│       └── tools/
│
└── clients/                        # Instâncias de clientes reais (IGNORADAS NO GIT)
    └── .gitkeep                    # Único arquivo rastreado desta pasta
```

---

## 3. Padrão de Banco de Dados (PostgreSQL Obrigatório)

> ⚠️ **Fim do SQLite:** Todos os agentes deste repositório utilizam **PostgreSQL**. O SQLite sofria com travamentos de arquivo (`database is locked`) sob mensagens concorrentes e não permitia múltiplos workers ou reinicializações seguras.

### Como a persistência funciona
1. **Agno Nativo (`agno_sessions`):**
   - Agentes usam `PostgresDb` (síncrono com pool SQLAlchemy/psycopg2) ou `AsyncPostgresDb` (assíncrono com `psycopg_async`).
   - O schema padrão é `public` ou o schema dedicado do cliente (`DB_SCHEMA`).
2. **Tabelas de Negócio:**
   - **Template SDR:** Tabelas prefixadas `{PREFIX}leads`, `{PREFIX}followup_queue` e `{PREFIX}products`. A inicialização executa o script `db/schema.sql`.
   - **Template WABA:** Tabelas `admin_sent_messages`, `paused_sessions`, `tool_errors`, `llm_usage_log`, `agent_transactions`. Cada módulo executa `ensure_table()` automaticamente na inicialização via `app.db`.

---

## 4. Passo a Passo para Implementar em um Novo Cliente

### Passo 1: Clonar o template para a pasta do cliente
```bash
cd /root/bf-agents-hub

# Para SDR com UazAPI:
cp -r templates/sdr-whatsapp-uazapi clients/nome-do-cliente

# OU para API Oficial (WABA):
cp -r templates/whatsapp-oficial-waba clients/nome-do-cliente

cd clients/nome-do-cliente
```

### Passo 2: Configurar as variáveis de ambiente (`.env`)
```bash
cp .env.example .env
nano .env
```
Preencha as credenciais obrigatórias:
- `DATABASE_URL`: Conexão PostgreSQL (ex: `postgresql://user:senha@localhost:5432/nome_db`).
- `PORT`: Porta dedicada (consulte `PORTS.md` para a próxima porta livre, ex: `7776`).
- `SLUG`: Identificador em minúsculas (ex: `cliente-x`).
- Chaves de API: `OMNIROUTE_API_KEY` ou `OPENROUTER_API_KEY`, e credenciais do WhatsApp (UazAPI ou Meta WABA).

### Passo 3: Inicializar o Banco de Dados
Para o template SDR, carregue o schema inicial:
```bash
# Executando o schema no PostgreSQL
psql -U postgres -d nome_db -f db/schema.sql
psql -U postgres -d nome_db -f db/seed_products.sql
```

### Passo 4: Customizar Regras de Negócio e Identidade
- **Template SDR (`config/`):**
  - `config/identity.py`: Nome do atendente, nome da empresa, produtos, tom de voz, objeções e regras de qualificação.
  - `config/cadence.py`: Textos das cadências de follow-up (dias 1 a 5).
  - `config/handoff.py`: Número do vendedor e grupo de WhatsApp para repasse.
- **Template WABA (`prompts/` e `tools/`):**
  - Ajuste os prompts do sistema e registre as tools específicas da API do cliente.

### Passo 5: Testar Localmente
Utilize sempre a venv compartilhada `/root/bf-agents-hub/.venv`:
```bash
/root/bf-agents-hub/.venv/bin/python3 -m uvicorn app:app --host 0.0.0.0 --port 7776
```
Em outro terminal, valide o healthcheck:
```bash
curl http://127.0.0.1:7776/health
```

### Passo 6: Criar o Serviço Systemd
Crie o arquivo `/etc/systemd/system/agno-<slug>.service`:
```ini
[Unit]
Description=Agno SDR - <Nome do Cliente>
After=network.target postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=/root/bf-agents-hub/clients/<slug>
EnvironmentFile=/root/bf-agents-hub/clients/<slug>/.env
ExecStart=/root/bf-agents-hub/.venv/bin/python3 -m uvicorn app:app --host 0.0.0.0 --port <PORTA> --workers 1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
Ative e inicie o serviço:
```bash
systemctl daemon-reload
systemctl enable --now agno-<slug>.service
systemctl status agno-<slug>.service
```

### Passo 7: Expor via Cloudflare Tunnel
Edite a configuração do tunnel (`~/.cloudflared/bf-os.yml`):
```yaml
ingress:
  - hostname: agentshub.bflabs.com.br
    path: /<slug>/.*
    service: http://127.0.0.1:<PORTA>
```
> ⚠️ **Atenção:** Mantenha rotas mais específicas antes de rotas genéricas. Reinicie o tunnel após alterar:
> `systemctl restart cloudflared-bf-os`

---

## 5. Cuidados Operacionais Críticos

### Git & Higiene de Repositório
- **Clientes Antigos Já Rastreados:**
  Se clientes legados na raiz (`cred-popular`, `ads-support`, etc.) tiverem sido commitados em branches anteriores, remova-os do rastreamento do Git **sem deletá-los do disco**:
  ```bash
  git rm -r --cached cred-popular/ ads-support/ bf-assist/ Agente-funil-maternidade/
  git commit -m "chore: desvincular instancias locais de clientes do git"
  ```
- **NUNCA suba `.env`:** Arquivos `.env` contêm chaves de API e senhas de banco. Apenas `.env.example` é versionado.

### Regra de Ouro do Atendimento SDR
1. **O Agente NUNCA se declara IA:** Se perguntado diretamente se é um robô ou IA, o agente desconversa com naturalidade e oferece conectar o lead com um consultor humano imediatamente.
2. **Debounce Obrigatório (`MessageBuffer`):** Leads de WhatsApp enviam frases quebradas ("oi", "tudo bem?", "quero saber o preço"). O `MessageBuffer` aguarda alguns segundos (ex: 8s) antes de disparar o agente, evitando respostas truncadas e custos desnecessários de LLM.
3. **Uvicorn Workers = 1:** O engine assíncrono do Agno (`AsyncPostgresDb`) não é seguro para forks. Execute sempre com `--workers 1`.
