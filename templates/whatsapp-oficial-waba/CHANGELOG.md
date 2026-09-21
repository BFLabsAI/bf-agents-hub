# Changelog — SBOT Webchat Agent

Registro cronológico de todos os ajustes, correções e melhorias realizados no agente.

---

## Instruções de preenchimento

Cada entrada deve usar um **timestamp ISO 8601 completo** como cabeçalho de seção:

```
## 2026-05-08T02:15:00-03:00 — Título curto da mudança
```

- Formato: `AAAA-MM-DDTHH:MM:SS±HH:MM` (data + hora local + offset de fuso)
- Fuso padrão do servidor: `America/Sao_Paulo` (UTC-3 no horário de Brasília, UTC-2 no verão)
- **Nunca usar apenas a data** — o horário é obrigatório para ordenação e rastreabilidade
- Entradas dentro do mesmo dia devem ter timestamps distintos
- Ordem: mais recente no topo
- Seções antigas sem timestamp mantêm o formato `## [Unreleased] — AAAA-MM-DD` (legado — não alterar retroativamente)

---

## 2026-05-08T03:49:00-03:00 — Gestão de templates WhatsApp e envio via template no Chat

**Contexto:** Necessidade de gerenciar templates WABA (criação, edição, exclusão) e permitir envio de templates tanto dentro quanto fora da janela de 24h no Chat.

**Arquivos modificados:**
- `sbot-dashboard/src/pages/Templates.tsx` (novo, 1195 linhas)
- `sbot-dashboard/src/pages/Chat.tsx`
- `sbot-dashboard/src/lib/api.ts`
- `sbot-dashboard/src/components/Layout.tsx`
- `sbot-dashboard/src/App.tsx`
- `sbot/app/admin_router.py`
- `sbot/.env`

**Templates.tsx — página nova de gestão:**
- Grid de cards com status/categoria badges por cor (APPROVED/PENDING/REJECTED/PAUSED/DISABLED)
- Abas de filtro por status + campo de busca por nome
- Skeleton loading no carregamento inicial
- **CreateModal** (slide-over): nome, idioma, categoria, header, body (detecção automática de variáveis `{{1}}` com geração de exemplos), footer, até 3 botões quick-reply + botão URL
- **EditModal**: mesmos campos (nome/idioma/categoria read-only), só exibido para templates REJECTED ou PAUSED
- Confirmação inline de exclusão (sem `window.confirm`)
- Comparação de status case-insensitive (`.toUpperCase()`)

**Chat.tsx — envio via template:**
- Botão "Enviar via template" no input da conversa ativa (janela aberta)
- Estado `forceTemplate` — quando ativo exibe seletor de cards de template estilo amber
- `handleSendMessage` verifica `windowStatus?.must_use_template === true || forceTemplate`
- Helper `getTemplateBodyText()` para preview do body no card
- Seletor out-of-window (janela expirada) preservado com visual azul/cinza
- Apenas templates APPROVED exibidos

**api.ts — novos endpoints:**
- `id?: string` adicionado à interface `WABATemplateAPI`
- `createTemplate(data)` → `POST /templates`
- `deleteTemplate(name, templateId?)` → `DELETE /templates?name=...&hsm_id=...`
- `editTemplate(templateId, components)` → `POST /templates/{templateId}`

**admin_router.py — novos endpoints proxy:**
- `GET /templates`: parâmetros `status`, `name`, `limit` repassados ao Meta; versão corrigida de `v23.0` → `v21.0`
- `POST /templates`: proxy para `POST graph.facebook.com/v21.0/{WABA_ID}/message_templates`
- `POST /templates/{template_id}`: proxy de edição para `POST graph.facebook.com/v21.0/{template_id}`
- `DELETE /templates`: proxy com `name` + `hsm_id` opcionais

**Layout.tsx:** `Templates` adicionado à seção "Operação" da sidebar (bug: estava definido em `navigation` mas ausente nos grupos de seção — nunca renderizava)

**App.tsx:** rota `/templates` adicionada com `RequireAuth`

**sbot/.env:** `WABA_BUSINESS_ACCOUNT_ID=1581379386619279` adicionado

---

## 2026-05-08T18:30:00-03:00 — Atendimentos (Chat) refeito no padrão visual do Web Chat

**Contexto:** O redesign anterior do Chat ficou abaixo do nível do Web Chat. O usuário aprovou o padrão do Web Chat e pediu paridade visual.

**Arquivo:** `sbot-dashboard/src/pages/Chat.tsx`

**Mudanças aplicadas (mesmo padrão visual do `WebChat.tsx`):**
- **Frame chrome WhatsApp** no topo da conversa: 3 dots emerald-300/400/500 + chip mono "WhatsApp Business · sessão #ABC123" + strip emerald-50 com estado da janela 24h
- **Filter pills** acima da busca: Todas / Ativas / IA Pausada / Autenticadas / Hoje, contadores via `useMemo` alimentado por `aiStatusMap`, gradient emerald-to-lime quando ativa
- **Lista lateral**: avatares 52×52 rounded-2xl com gradient emerald-to-lime, dot online pulsante, barra emerald esquerda + gradient `from-emerald-50 to-lime-50` quando selecionado, chips "IA Ativa/IA Pausada/Auth", preview da última mensagem em itálico, financial chip
- **Header da conversa**: avatar 56×56 ring branco + dot pulsante, sub-info "WhatsApp · telefone · tempo relativo" com WindowBadge integrado, quick actions (PauseCircle/PlayCircle, Sparkles, Detalhes pill gradient)
- **Mensagens**: markdown rico (H1/H2/H3, listas, code blocks, blockquote, tabelas, links com ExternalLink), bubbles agente em gradient emerald-500→600 com `darkBg` adaptado, agrupamento de mensagens consecutivas (avatar só no primeiro), separadores de data como pílulas centrais "Hoje/Ontem/data", `BubblePattern` SVG sutil no fundo
- **Input premium**: container com shadow emerald colorida, focus-within intensifica, botão Send em pill gradient, banner de feedback embutido (sem deslocar layout). Out-of-window: scroll horizontal de cards de template com chip de idioma + preview, botão grande "Enviar template"
- **Drawer redesenhado**: hero gradient emerald-600→800 com DotPattern + blobs lime/emerald, ring-4 white/30 no avatar 96×96, status pill `bg-white/15 backdrop-blur`. Bloco "Insights" (Tópico detectado / Total mensagens / Status IA via heurística), toggle Modo IA como hero element, card de Resumo IA com markdown styled, grid 2-col de metadata (Financeiro/Auth/Telefone/Criado) + Session ID full-width mono
- **Empty states ilustrados**: círculo gradient com MessageCircle 24×24 + sparks lime + check decorativo

**Funcionalidades preservadas integralmente:** `fetchUsers`, `fetchActiveChatSessions`, `fetchChatMessages`, `fetchAIStatus`, `pauseAI`/`resumeAI`, `fetchWindowStatus`, `fetchTemplates`, `sendMessage`/`sendTemplate`, `generateSummaryAPI`, mobile responsive, componente `WindowBadge`.

---

## 2026-05-08T17:45:00-03:00 — Web Chat redesenhado com browser frame e insights heurísticos

**Arquivo:** `sbot-dashboard/src/pages/WebChat.tsx`

**Mudanças visuais:**
- **Browser-frame chrome**: barra fake de browser com 3 dots emerald + URL mono "sbot.org.br/chat · sessão #ABC123" + ícone Globe, reforçando contexto "web"
- **Strip Read-only persistente**: `bg-emerald-50 border-emerald-200`, ícone Lock + "Modo leitura · Atendimento autônomo da Dra. SBOT" — deixa o caráter somente leitura permanente
- **Filter pills** (Todas / Autenticadas / Guest / Hoje / 7 dias) com gradient emerald-to-lime quando ativa, contadores dinâmicos via `useMemo`, lógica client-side (filtra por `session_id` contendo "person-", `created_at`, etc.)
- **Lista**: avatares 52×52 rounded-2xl com Globe (guest sem nome) ou inicial, chip Auth/Guest, contagem + tempo relativo via `date-fns`, preview italic da **última mensagem do USUÁRIO** (não do agente)
- **Mensagens com markdown rico**: H1/H2/H3 progressivos, `<ul>` bullets emerald, `<ol>` numeradas emerald-700, `<code>` inline `bg-emerald-100`, code block `bg-navy-900 text-white border-emerald-500` esquerda 3px, `<blockquote>` borda emerald-400 4px, tabelas com header `bg-emerald-50`, links com `ExternalLink`. Bubbles do agente em gradient emerald-500→600 text-white com `darkBg` adaptando inline elements. Date separators "Hoje/Ontem/data"
- **Drawer com Hero gradient** emerald-600→800 + DotPattern + blobs, badge "Web Chat · Modo leitura"
- **Seção Insights no topo do drawer** (3 mini-cards): Tópico detectado / Categoria / Tools chamadas — heurística baseada em keywords nas mensagens do usuário ("inscrição", "PIX", "boleto") e parsing de `[Used tools: ...]` raw
- **CircuitPattern SVG** (linhas + nós em emerald-500/[0.08]) substitui dot pattern, dando toque "web/digital"
- **Empty state inicial**: globo SVG animado `animate-[spin_30s_linear_infinite]` + pills de capabilities (Resumos IA / Insights / Read-only)

---

## 2026-05-08T17:00:00-03:00 — Dashboard redesign premium com hero, funil de conversão e bandas verdes

**Arquivo:** `sbot-dashboard/src/pages/Dashboard.tsx`

**Mudanças visuais e estruturais:**
- **Hero stats strip** no topo: container gradient `from-emerald-50 via-white to-lime-50` com `DotPattern` + `GridPattern` SVG e blobs blur. 3 KPIs hero em **mega numbers** `text-5xl lg:text-7xl font-black tracking-tighter` com gradient text-clip emerald-to-lime (Atendimentos, Receita R$, Tx. Conversão). Pills de período embutidos (Hoje · 7 · 30 · 90 · Personalizado) — substitui card de filtro genérico
- **Section ritmo**: SBOT Atendimento em fundo branco com `MegaNumber "01"`. Web Rag Agent dentro de full-bleed `GreenBand` com `MegaNumber "02"` — alterna ritmo visual
- **Funil de conversão** (substitui 5 KPIs financeiros isolados): 3 cards conectados por `ArrowRight` pulsantes — Gerado → Confirmado → Conversão. Replicado para WhatsApp e Web
- **Donuts com número no centro**: posicionamento absoluto + flexbox sobre `ResponsiveContainer`. Legenda como chips coloridos abaixo
- **Charts narrativos**: mini-stat acima de cada gráfico ("Pico em Quarta às 14h — 47 msgs"), `<defs><linearGradient>` emerald-500 → lime-400, `DotPattern` SVG sutil nos cards
- **Listas recentes redesenhadas** (Usuários/Conversas/Sessões Web): avatares 44×44 com gradient + dot online `animate-pulse`, hover emerald-300, badges em emerald/lime/slate
- **Modal de datas premium**: header com ícone gigante, pills de atalho (7/14/30/90/Mês atual), botão Aplicar em gradient emerald-to-lime
- **Loading state**: skeleton screens em vez de spinner azul
- **Análise IA card**: ícone Bot grande em container gradient, badges Modelo/Última análise, 4 categorias como FeatureGrid cards estilo User Guide

**Paleta unificada** — purgado todo violet/orange/red/amber/blue:
- `FINANCIAL_STATUS_COLORS`: `#10B981` (Quite/Adimplente), `#A3E635` (Débito → lime), `#065F46` (Não sócio → emerald-escuro), `#6B7280` (Não informado)
- `getFinancialStatusBadgeClass`: somente classes em emerald/lime/slate
- KPICards `color="violet|orange|red"` substituídos por novo componente local `StatCard` (apenas emerald/lime/slate)

---

## 2026-05-08T16:15:00-03:00 — Layout/Sidebar redesenhado com seções e identidade SBOT

**Arquivo:** `sbot-dashboard/src/components/Layout.tsx`

**Mudanças visuais:**
- **Faixa vertical gradient emerald-lime** na borda esquerda da sidebar (decorativa)
- **Logo SBOT com glow emerald blur** atrás
- **Navegação agrupada em seções com labels**: "OPERAÇÃO" (Dashboard, Atendimentos, Web Chat) e "RECURSOS" (Documentos, Configurações, Guia de Usuário). Labels em `text-[10px] uppercase tracking-widest text-slate-400` + linha gradient emerald sutil ao lado
- **Item ativo dramatizado**: gradient `from-emerald-500 to-lime-500`, sombra colorida `shadow-[0_8px_24px_-8px_rgba(16,185,129,0.6)]`, texto/ícone/ChevronRight brancos. Brilho interior `bg-gradient-to-br from-white/20 to-transparent`
- **Hover state**: borda emerald-100 aparece + ícone com micro-scale 110% + transição 200ms
- **Dot pulsante "ao vivo"** no item Atendimentos — `animate-pulse emerald-500`
- **User card hero** no rodapé: `SidebarDotPattern` SVG, blob emerald, avatar gradient com `ring-4 ring-white` e dot online `animate-pulse`, role em emerald-700 uppercase
- **Botão Sair**: hover emerald-50 + emerald-700 (substituindo rose-700 que estava fora da paleta)
- **Header**: breadcrumb "Início › [página atual]" acima do título; pill "Sistema Operacional" em gradient `emerald-50 → emerald-100` com dot `animate-pulse emerald-500` (substituindo `neon-blue`); divider gradient `emerald-200 via slate-200 to transparent`
- **Mobile**: botão hambúrguer com borda emerald-100 + hover emerald-50

**Funcionalidades preservadas:** `navigation` array, `handleLogout`, `isSidebarOpen`, `useLocation/useNavigate`, classe `h-24` do header, `max-w-7xl` do main, comportamento `lg:translate-x-0 lg:static`.

---

## 2026-05-08T15:30:00-03:00 — User Guide premium: hero, mockups, bandas verdes, ilustrações

**Arquivo:** `sbot-dashboard/src/pages/UserGuide.tsx` (reescrito do zero)

**Contexto:** Versão anterior do User Guide tinha cores fora da paleta (azul/âmbar/rosa/violeta) e poucos elementos visuais. Usuário pediu visual premium com paleta SBOT pura, elementos ilustrativos grandes, e bandas verdes alternando com brancas.

**Estrutura visual nova:**
- **Hero section** com gradient mesh, padrão de grid SVG decorativo, blobs blur, ornamento SVG, ícone gigante 24×24 em container gradient com sparks flutuantes, mini-stats em cards
- **MegaNumber** (números gigantes "01"-"09" em gradient text-clip emerald-to-lime, 7-9rem) ao lado de cada cabeçalho de seção
- **GreenBand** (full-bleed `-mx-4 lg:-mx-8` com gradient `from-emerald-50 via-white to-lime-50/60`, `DotPattern` SVG, 3 blobs blur) alternando com seções brancas
- **Reading progress bar** fixa no topo (gradient emerald-to-lime que preenche conforme rola)
- **Sidebar TOC** com `IntersectionObserver` rastreando seção ativa, border-left emerald no item ativo
- **Tabs sticky**: "Dashboard" e "Dra. SBOT" com pills em gradient quando ativo
- **DashboardMockupCard** — preview visual de KPI card com mini-gráfico de barras gradiente e efeito stack 3D
- **PhoneMockup** — mockup completo de celular com chat WhatsApp da Dra. SBOT (mensagens reais simuladas)
- **AgentAvatar** — avatar circular gigante (256px) com ornamento SVG girando em loop infinito, partículas flutuantes
- **WindowStatusShowcase** — três cards visuais grandes mostrando estados da janela 24h ("24h", "<3h", "0h") com dots pulsando
- **FlowDiagram** — diagrama horizontal de 7 nodes conectados por setas, numerados, com hover state
- **ChannelComparison** — dois cards lado a lado (WhatsApp/Web), um com fundo emerald-escuro e DotPattern
- **StatsStrip** — 4 estatísticas em mega numbers (3h, 0s, 24h, ∞)
- **PullQuote** — bloco de citação com aspas SVG decorativas em emerald-100
- **Document Stack** — ilustração de 3 documentos empilhados em 3D (rotacionados)
- **FaqItem** accordion com chevron rotacionando + borda emerald + shadow colorida quando aberto

**Paleta SBOT pura**: somente `emerald` (50-900), `lime` (50-500), `slate` (50-700), `navy-900`, `white`. Removido todo blue/amber/rose/violet/orange.

**Naming corrigido**: persona única "Dra. SBOT" operando em dois canais (WhatsApp + Web). "Ítalo" (nome interno do arquivo) não aparece em lugar nenhum no guia.

---

## 2026-05-08T14:00:00-03:00 — Adicionada página /user-guide com aba no menu

**Contexto:** Página de manual operacional acessível pelo menu lateral, abaixo de Configurações.

**Novos arquivos:**
- `sbot-dashboard/src/pages/UserGuide.tsx` — página com tabs Dashboard/Agentes
- `sbot-dashboard/user-guide.md` — markdown source do conteúdo
- `sbot-dashboard/ia-guide.md` — markdown source do guia de agentes

**Mudanças:**
- `sbot-dashboard/src/App.tsx`: nova rota `/user-guide` envolvida por `RequireAuth`
- `sbot-dashboard/src/components/Layout.tsx`: novo item "Guia de Usuário" no `navigation` com ícone `BookOpen`

**Conteúdo do guia (Dashboard tab)**: visão geral, navegação, aba Dashboard (KPIs WhatsApp + Web, análise IA, gráficos), aba Atendimentos (janela 24h, envio de mensagens, controle da IA, painel de detalhes), aba Web Chat (somente leitura, comparação WhatsApp vs Web), aba Documentos (busca e preview), FAQ.

**Conteúdo do guia (Agentes tab)**: persona única Dra. SBOT em dois canais, identificação por canal, fluxo completo de inscrição, métodos de pagamento, comportamentos garantidos (dados frescos, confirmação explícita, IDs internos protegidos), eventos reconhecidos automaticamente (CBOT, SGEE, SBOT Lab, Módulos, Anuidade, TEOT), limitações, FAQ.

---

## 2026-05-08T13:00:00-03:00 — Padrão de timestamp adicionado ao Changelog

**Arquivos:**
- `sbot/CHANGELOG.md`: adicionada seção "Instruções de preenchimento" no topo definindo formato ISO 8601 obrigatório (`AAAA-MM-DDTHH:MM:SS±HH:MM`), fuso `America/Sao_Paulo`, ordem mais recente no topo
- `sbot-dashboard/user-guide.md`: instruções equivalentes com nota sobre atualização de seções existentes

**Motivação:** Antes só havia data — entradas dentro do mesmo dia ficavam ambíguas. Timestamp completo garante ordenação e rastreabilidade.

---

## 2026-05-08T03:00:00-03:00 — Webchat: detecção de logout por expiração/cessação de token

**Problema:** Ao deslogar do iTarget, o widget continuava enviando o mesmo Bearer token (ainda válido criptograficamente) e o agente mantinha a sessão autenticada.

**Implementação (`app/webchat_agent.py`):**
- `_is_token_expired(token)`: decodifica JWT sem verificação de assinatura; se `exp` estiver no passado → downgrade para guest
- `_uuid_person: dict[str, int]`: rastreia `uuid → person_id` autenticado por requisição
- Ao primeiro request sem token de um UUID que estava autenticado → retorna `_SESSION_ENDED_MSG` diretamente sem chamar o LLM
- `uuid` passado explicitamente para `generate_reply` via `payload.uuid`

**Limitação conhecida:** Se o widget continuar enviando o token mesmo após logout (JWT válido, mas sessão iTarget invalidada no servidor), o backend não consegue detectar — exige integração no widget para limpar o token.

**Arquivos modificados:**
| Arquivo | Alterações |
|---|---|
| `app/webchat_agent.py` | `_is_token_expired`; `_uuid_person` tracking; `_SESSION_ENDED_MSG`; `uuid` em `generate_reply` |
| `app/webchat_router.py` | `uuid=payload.uuid` passado para `generate_reply` |

---

## 2026-05-08T02:25:00-03:00 — Dashboard: conversas sem registro em users_sbot não apareciam

**Problema:** `Chat.tsx` construía `filteredUsers` filtrando `users` (tabela `users_sbot`) por `chatHistorySessions`. Sessões em `agno_sessions` sem entrada em `users_sbot` (ex: login por CPF antes do fix anterior) ficavam invisíveis no painel "Atendimentos".

**Correção (`sbot-dashboard/src/pages/Chat.tsx`):** `fetchData` gera entradas sintéticas `UserSbotAPI` para cada sessão ativa em `agno_sessions` que não tem correspondência em `users_sbot`. O `phone` da entrada sintética é o `session_id` retornado por `/active-chat-sessions`.

---

## 2026-05-08T02:20:00-03:00 — Fix: identify_by_document não populava users_sbot

**Problema:** Login por CPF (`identify_by_document`) chamava `_extract_and_store` mas não chamava `ulog.upsert`, então o usuário autenticado via CPF nunca aparecia na tabela `users_sbot` e ficava invisível no dashboard "Atendimentos".

**Correção (`tools/identity.py`):** Adicionado `ulog.upsert(...)` imediatamente após `_extract_and_store` no bloco de sucesso de `identify_by_document`, espelhando o comportamento já existente em `_restore_or_get_me` (usado pelo login por telefone).

---

## 2026-05-08T02:06:00-03:00 — Auto-resume da IA após inatividade (loop horário)

**Motivação:** Sessões pausadas por human takeover ficavam bloqueadas indefinidamente se o operador esquecia de reativar.

**Novo módulo `app/auto_resume.py`:**
- Loop asyncio iniciado no startup (`start_auto_resume_loop()`), roda a cada hora (`CHECK_INTERVAL_SECONDS = 3600`)
- `_run_check()` itera todas as sessões pausadas (`get_paused_with_timestamps()`) e retoma se:
  1. **Usuário respondeu após a pausa** (`last_user_ts > paused_at`) — IA retoma imediatamente para responder
  2. **Pausa ≥ `AUTO_RESUME_HOURS` (3h) sem nova mensagem** — evita sessões bloqueadas eternamente
  3. **Linha legada sem `paused_at`** — fallback pela idade da última mensagem do usuário
- Timestamp da última mensagem do usuário extraído de `agno_sessions.runs` (formato double-encoded do Agno)

**`app/whatsapp_api.py`:**
- `startup` convertido de `def` para `async def`
- `asyncio.create_task(start_auto_resume_loop())` adicionado ao startup

**Arquivos modificados:**
| Arquivo | Alterações |
|---|---|
| `app/auto_resume.py` | **Novo** — loop de auto-resume com 3 critérios de retomada |
| `app/whatsapp_api.py` | `startup` → `async def`; `create_task(start_auto_resume_loop())` |

---

## 2026-05-07T23:45:00-03:00 — Human Takeover — pausa de IA e envio de mensagens pelo admin

**Contexto:** Operadores precisavam pausar o atendimento automático, enviar mensagens diretamente pelo dashboard e ter a IA pausada automaticamente ao enviar.

**Novo módulo `app/pause_registry.py`:**
- `_paused: set[str]` em memória + tabela SQLite `paused_sessions` com colunas `phone`, `paused`, `paused_at`, `updated_at`
- `pause(phone)` / `resume(phone)` / `is_paused(phone)` / `list_paused()` / `get_paused_with_timestamps()`
- `inject_assistant_message(phone, content, db_path)` — insere run sintético em `agno_sessions.runs` (double-encoded) para as mensagens do admin fazerem parte do contexto do agente
- `set_bridge(b)` / `_evict(phone)` — remove agente do pool em memória para re-leitura do DB na próxima mensagem
- `AUTO_RESUME_HOURS: float = 3.0`
- `load_from_db()` chamado no import para restaurar estado após restart

**Novo módulo `app/admin_message_log.py`:**
- Tabela SQLite `admin_sent_messages`: `phone`, `content`, `template_name`, `wamid`, `sent_at`
- `insert()` e `query_by_phone()` — persiste e recupera mensagens enviadas pelo painel

**Novos endpoints em `app/admin_router.py`:**
| Endpoint | Descrição |
|---|---|
| `GET /sessions/{phone}/ai-status` | Retorna `{phone, ai_paused}` |
| `POST /sessions/{phone}/pause-ai` | Pausa a IA para o número |
| `POST /sessions/{phone}/resume-ai` | Retoma a IA para o número |
| `GET /users/{phone}/window-status` | Status da janela de 24h do WABA |
| `GET /templates` | Lista templates WABA aprovados |
| `POST /send-message` | Envia texto livre via WABA, pausa IA e injeta na memória do agente |
| `POST /send-template` | Envia template WABA, pausa IA e injeta na memória do agente |

`_query_messages()` atualizado para mesclar `admin_sent_messages` com as mensagens do agno — mensagens do admin aparecem no histórico do chat.

**`app/whatsapp_bridge.py`:**
- `AgentRuntime.evict(phone)` — remove sessão do pool in-memory
- Guard no início de `generate_reply()`: se `pause_registry.is_paused(phone)`, retorna `""` sem chamar o LLM

**`app/whatsapp_api.py`:**
- Guard `if reply:` antes de `waba.send_text()` — previne envio de string vazia à API Meta quando IA está pausada
- `pause_registry.set_bridge(bridge)` após criação do bridge

**`app/waba_client.py`:**
- Novo método `send_template(to, template_name, language, components)` para templates WABA

**Arquivos modificados:**
| Arquivo | Alterações |
|---|---|
| `app/pause_registry.py` | **Novo** — registry in-memory + SQLite; inject; eviction |
| `app/admin_message_log.py` | **Novo** — log de mensagens do admin |
| `app/admin_router.py` | 7 novos endpoints; `_query_messages` mescla admin_sent |
| `app/whatsapp_bridge.py` | `evict()`; guard `is_paused` em `generate_reply` |
| `app/whatsapp_api.py` | Guard `if reply:`; `set_bridge()` após criação do bridge |
| `app/waba_client.py` | `send_template()` |

---

## 2026-05-07T23:00:00-03:00 — Dashboard: envio WABA com janela 24h, badge IA e input adaptativo

**`sbot-dashboard/src/lib/api.ts`:**
- Novas interfaces: `WindowStatusAPI`, `WABATemplateAPI`, `SendMessageResponse`, `AIStatusAPI`
- Novas funções: `fetchWindowStatus`, `fetchTemplates`, `sendMessage`, `sendTemplate`, `fetchAIStatus`, `pauseAI`, `resumeAI`

**`sbot-dashboard/src/pages/Chat.tsx`:**
- Badge de status da janela de 24h (`WindowBadge`) no header da conversa
- Input adaptativo: texto livre dentro da janela; seletor de template fora da janela
- `handleSendMessage()` chama o backend e auto-seta `aiPaused=true` no sucesso
- Badge de status da IA no header: âmbar "IA pausada" / verde "IA ativa"
- Toggle de "Modo IA" no painel de detalhes (pausa/retoma a IA manualmente)

**Novo componente `sbot-dashboard/src/components/WindowBadge.tsx`:**
- Verde: dentro da janela de 24h (exibe horas restantes)
- Âmbar: expirando em menos de 3h
- Vermelho: fora da janela (obrigado a usar template)

**Arquivos modificados:**
| Arquivo | Alterações |
|---|---|
| `src/lib/api.ts` | Interfaces e funções WABA + pause/resume |
| `src/pages/Chat.tsx` | WindowBadge; input adaptativo; badge IA; toggle pause |
| `src/components/WindowBadge.tsx` | **Novo** — badge de janela de 24h |

---

## 2026-05-07T22:00:00-03:00 — Fix: mensagens do admin persistidas no histórico do chat

**Problema:** Mensagens enviadas pelo painel admin não apareciam no histórico de conversa do dashboard.

**Causa:** O endpoint `/chat-sessions/{phone}/messages` só lia `agno_sessions.runs`; mensagens enviadas pelo admin não eram armazenadas em nenhuma tabela consultada.

**Correção:** `_query_messages()` em `admin_router.py` passou a mesclar os registros de `admin_sent_messages` com as mensagens do agno, ordenando por `created_at`.

---

## 2026-05-07T21:30:00-03:00 — Fix: painel de detalhes quebrava layout; auto-scroll; resumo IA

**Fix 1 — painel de detalhes quebrava o layout (`Chat.tsx`, `WebChat.tsx`):**
- Painel usava `lg:static lg:block` tornando-se parte do flex container e deslocando a área de chat
- Corrigido para overlay fixo: `fixed inset-y-0 right-0 z-50 w-96`
- Padrão fechado (`infoOpen = false`); estado unificado (substituiu `showMobileInfo` + `infoCollapsed`)

**Fix 2 — chat não rolava para a última mensagem (`Chat.tsx`, `WebChat.tsx`):**
```tsx
useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'auto' });
}, [chatHistory]);
```
`<div ref={messagesEndRef} />` inserido ao final da lista de mensagens.

**Fix 3 — Resumo IA retornava "Erro ao gerar resumo" (`admin_router.py`):**
- Frontend enviava `messages` como string pré-formatada; backend tentava `for m in messages` iterando caractere a caractere
- Corrigido para `if isinstance(messages, str): messages_text = messages` antes do `join`

---

## 2026-05-07T20:00:00-03:00 — Fix: logo SBOT quebrada após deploy

**Problema:** Logo renderizada como `/sbot/dashboardSBOT_logo.png` (sem separador entre base e nome do arquivo).

**Causa:** `vite.config.ts` tinha `base: '/sbot/dashboard'` sem trailing slash. `import.meta.env.BASE_URL` retornava `/sbot/dashboard` e a concatenação com o nome do arquivo ficava sem `/`.

**Correção (`sbot-dashboard/vite.config.ts`):**
```
base: '/sbot/dashboard'  →  base: '/sbot/dashboard/'
```

---

## 2026-05-07T19:50:00-03:00 — Resiliência: RetryingTransport no cliente HTTP (sbot)

**Problema:** Erros transientes (`RemoteProtocolError`, `ReadError`) nas chamadas à API iTarget causavam falhas que exigiam nova mensagem do usuário.

**Correção (`app/itarget_client.py`):**

```python
class _RetryingTransport(httpx.AsyncBaseTransport):
    # Retries GET/HEAD/OPTIONS até 2x com backoff 0.2s/0.4s
    # Nunca retenta POST (não idempotente)
```

- `httpx.AsyncHTTPTransport(verify=ITARGET_SSL_VERIFY, retries=1)` como inner transport
- `_RetryingTransport(inner, max_retries=2)` como transport final do `AsyncClient`

---

## 2026-05-07T19:40:00-03:00 — Humanização do prompt Web / Dra. SBOT Auth (12 áreas)

**Arquivo:** `prompts/sbot_auth.py`

Mesmas 12 áreas humanizadas da versão WhatsApp, adaptadas para o canal web:
- Persona: "Dra. SBOT" (não "Ítalo")
- Formatação: markdown `**negrito**` (não `*negrito*` WhatsApp)
- Sem emojis no texto principal
- Adicionada instrução VALOR FINAL (estava ausente na versão anterior)

---

## 2026-05-07T19:30:00-03:00 — Humanização do prompt WhatsApp (12 áreas)

**Arquivo:** `prompts/italo.py`

Melhorias aplicadas em:
1. Tom geral — concierge caloroso, frases curtas, primeira pessoa
2. Saudação inicial — contextualizada ao horário, sem formalidade excessiva
3. Template "Mais informações" — resposta concierge completa com detalhes, valor e CTA claro
4. Regra de valor único — quando `associatedAmount == amount`, exibe apenas um valor (sem redundância)
5. Fluxo de inscrição — confirmação explícita antes de apresentar métodos de pagamento
6. Fluxo de pagamento — linguagem encorajadora, não burocrática
7. Tratamento de não-sócio — empático, sem julgamento
8. Anuidade — explicação clara do que é e por que aparece
9. CBOT — identificado como "Congresso Anual SBOT"; buscar com "Congresso"
10. Respostas de erro — humanas, com sugestão de próximo passo
11. Confirmação de inscrição — celebração discreta, instrução imediata
12. Encerramento — convite aberto, sem robótico "posso ajudar em algo mais?"

---

## 2026-05-07T19:20:00-03:00 — Fix: fluxo alreadySubscribed pulava confirmação de pagamento

**Problema:** `nextActionHint` em `alreadySubscribed` dizia "perguntar método de pagamento", fazendo o agente pular a etapa de confirmação "deseja pagar agora?".

**Correção (`tools/events.py`):** `nextActionHint` alterado para `"ask_if_user_wants_to_pay_first_then_ask_method"` — obriga o agente a confirmar o interesse antes de apresentar as opções.

---

## 2026-05-07T19:10:00-03:00 — Melhoria: detalhes de evento enriquecidos com dados do catálogo

**Problema:** `get_event_context` usava apenas o endpoint `/detailing`, que não retorna datas nem localização. A resposta ao usuário ficava vaga.

**Correção (`tools/events.py`):**
- Se `ctx.available_events` estiver vazio, carrega o catálogo via `list_events` antes de detalhar
- `detail_summary` é enriquecido com `startDate`, `endDate`, `location` do item do catálogo
- Descrição HTML: remove blocos `<style>` e `<script>` antes do strip das tags, evitando CSS no texto

---

## 2026-05-07T19:05:00-03:00 — Fix: find_event_by_name com falso-positivo em números

**Problema:** A busca fuzzy incluía tokens puramente numéricos (IDs, anos) como palavras-chave, causando matches incorretos.

**Correção (`tools/events.py`):** Filtro na tokenização: palavra só entra na busca se `len(word) > 3 and not word.isdigit()`.

---

## 2026-05-07T19:00:00-03:00 — Fix: inscrição de não-sócio causava erro não tratado

**Problema:** API retornava HTTP 422 ao tentar inscrever não-sócio em eventos restritos. O erro chegava ao agente sem contexto acionável.

**Correção (`tools/subscription.py`):**
- Pre-check antes da chamada API: se `ctx.member_status` for `"não sócio"` → retorna `{"status": "not_a_member"}` sem chamar a API
- No bloco `except HTTPStatusError`: HTTP 422 também mapeado para `not_a_member`

---

## 2026-05-07T18:50:00-03:00 — Fix: botões do carousel WhatsApp descartados silenciosamente

**Problema:** Cliques nos botões do carousel (`info_*`, `sub_*`) chegavam como `msg_type="button"` (não `"interactive"`), sem handler no bridge. A mensagem era silenciosamente descartada e o agente não respondia.

**Correção (`app/whatsapp_bridge.py`):** Adicionado `elif msg_type == "button":` que lê `msg["button"]["payload"]` e converte para texto natural antes de passar ao agente.

**Formato dos botões (`app/waba_client.py`):** Confirmado que carousel cards exigem `type: "quick_reply"` (não `"reply"`). Dois botões por card: `info_{id}` → "Mais informações", `sub_{id}` → "Inscrever-se".

**Arquivos modificados:**
| Arquivo | Alterações |
|---|---|
| `app/whatsapp_bridge.py` | Handler `elif msg_type == "button"` para cliques em carousel |
| `app/waba_client.py` | Botões do carousel confirmados como `quick_reply`; dois botões por card |
| `tools/subscription.py` | Pre-check `not_a_member`; HTTP 422 → `not_a_member` |
| `tools/events.py` | Filtro numérico em `find_event_by_name`; enriquecimento de catálogo; strip HTML com style/script; `nextActionHint` alreadySubscribed |
| `prompts/italo.py` | Humanização completa (12 áreas) |
| `prompts/sbot_auth.py` | Humanização completa para web (12 áreas) + VALOR FINAL |
| `app/itarget_client.py` | `_RetryingTransport` com backoff em GET |

---

## [Unreleased] — 2026-05-07

### Bug: PIX gerado com valor errado quando usuário já estava inscrito

**Problema:** Usuário (CPF 083.103.938-81) recebia a mensagem de erro "Valor informado errado (você já está inscrito no evento 58º Congresso, com valor pendente R$ 502,50)" mas o PIX era gerado pelo valor R$ 445,00 — valor de outro evento. A causa era o agente chamando `list_payment_plans` em paralelo com outros tools, ignorando a inscrição existente e usando o plano de pagamento de um evento diferente.

**Causa raiz (duas frentes):**

1. **`list_payment_plans` sem guarda**: quando `ctx.account_receive_ids` já estava preenchido (usuário já inscrito), o tool continuava chamando a API — que retornava 422 "valor informado errado", e o agente usava o contexto stale do evento anterior.

2. **`get_event_context` chamava `list_payment_plans` para usuários já inscritos**: a condição `if category_registered` não excluía o caso `already_subscribed=True`, causando a chamada indevida.

**Correção (`tools/subscription.py` → `list_payment_plans`):**
```python
# Guarda adicionada no topo da tool:
if ctx.account_receive_ids:
    return json.dumps({
        "status": "already_subscribed_pending_payment",
        "accountReceiveIds": ar_ids,
        "nextAction": "call_list_payment_methods",
        "nextActionHint": "O usuário já possui inscrição. NÃO chame create_subscription. Chame list_payment_methods AGORA."
    })
```

**Correção adicional — classificação do erro 422 "já inscrito"**: quando a API retorna 422 com body contendo "já inscrito", "valor pendente" ou "valor informado", o tool agora classifica como `already_subscribed_pending_payment` em vez de `domain_error`, evitando o ciclo incorreto de `call_list_categories`.

**Correção (`tools/events.py` → `get_event_context`, Step 4):** `list_payment_plans` agora só é chamado quando `category_registered=True` **e** `already_subscribed=False`:
```python
if category_registered and not already_subscribed:
    # chama list_payment_plans
```

**Arquivos modificados:**
| Arquivo | Alterações |
|---|---|
| `tools/subscription.py` | Guarda `account_receive_ids`; classificação correta do 422 "já inscrito" |
| `tools/events.py` | `list_payment_plans` skipado para `already_subscribed=True` em `get_event_context` |

---

### Bug: ID do evento exposto na bolha de chat ao clicar no carrossel

**Problema:** Ao clicar em "Mais informações" no carrossel de eventos do webchat, a bolha exibia "Quero mais informações sobre o evento 4802" — expondo o ID interno ao usuário.

**Causa:** O payload do botão do carrossel usava `activityScheduleId` numérico em vez do título do evento.

**Correção (`app/web_templates.py`):** Tanto `render_event_carousel` quanto `render_event_list` agora usam o título do evento no payload:
```python
# Antes:
btn_payload = _attr_payload(f"Quero mais informações sobre o evento {aid}")

# Depois:
title_raw = e.get("title", "Evento")
btn_payload = _attr_payload(f"Quero mais informações sobre {title_raw}")
```

O agente recebe "Quero mais informações sobre Inscrição Para o 58º Congresso Anual Sbot" e chama `find_event_by_name` — que faz o match e obtém o ID internamente, sem nunca expô-lo ao usuário.

**Regra adicionada no prompt** (`prompts/sbot_auth.py`): quando a mensagem vier no formato `"Quero mais informações sobre {nome}"` sem ID numérico, chamar `find_event_by_name` imediatamente.

**Correção em `find_event_by_name`** (`tools/events.py`): `nextActionHint` corrigido de `event_detail` → `get_event_context`.

**Arquivos modificados:**
| Arquivo | Alterações |
|---|---|
| `app/web_templates.py` | Payload dos botões usa título do evento |
| `tools/events.py` | `nextActionHint` de `find_event_by_name` corrigido |
| `prompts/sbot_auth.py` | Regra para mensagens com nome de evento sem ID |

---

### Bug: troca de usuário logado não redefinia o agente

**Problema:** Ao trocar de conta logada no app (ou clicar em deslogar e logar com outra conta), o agente continuava respondendo como o usuário anterior — mantendo nome, histórico e contexto da sessão antiga. Especialmente crítico em sessões com UUID (`sbot-web-auth-{uuid}`) que podem ser reaproveitadas por pessoas diferentes no mesmo dispositivo.

**Causa:** O `WebchatRuntime` mantinha um pool em memória de bundles por `session_id`. Ao reutilizar o mesmo `session_id` com um token Bearer diferente, o bundle antigo continuava sendo usado sem verificação de identidade.

**Correção (`app/webchat_agent.py`):** Detecção em duas camadas antes de qualquer operação:

**Camada 1 — JWT:** decode do campo `sub` do token Bearer antes de buscar o bundle. Se `sub` diferir do `ctx.person_id` armazenado, o bundle é descartado imediatamente:
```python
jwt_person_id = _person_id_from_token(access_token)
existing_bundle = self._auth_bundles.get(session_id)
if existing_bundle and jwt_person_id and existing_bundle.ctx.person_id != int(jwt_person_id):
    self._reset_auth_bundle(session_id)
```

**Camada 2 — API (`get_me`):** após `get_me()`, se o `personId` retornado pela API diferir do `ctx.person_id` (captura casos onde o JWT `sub` não era parseável), o bundle é descartado e recriado:
```python
if api_person_id and bundle.ctx.person_id and bundle.ctx.person_id != int(api_person_id):
    self._reset_auth_bundle(session_id)
    bundle = self._get_auth(session_id)
```

**Logout path:** quando `access_token` está vazio (deslogado), o guest agent da sessão é removido do pool para evitar estado stale no próximo login.

**Helper adicionado:** `_person_id_from_token(token)` — decodifica o payload JWT sem verificação de assinatura e retorna o `sub` claim.

**Arquivos modificados:**
| Arquivo | Alterações |
|---|---|
| `app/webchat_agent.py` | Detecção de troca de usuário em dois níveis; `_reset_auth_bundle`; `_reset_guest_session`; `_person_id_from_token` |

---

### Bug: campo técnico `statusSubscription` exposto nas mensagens

**Problema:** O agente enviava mensagens ao usuário contendo o campo técnico `statusSubscription` (ex: "statusSubscription: 1") em vez de uma descrição legível.

**Correção (`tools/subscription.py` → `my_subscriptions`):** Adicionado campo `statusLabel` com texto legível junto ao campo numérico:
```python
_STATUS_LABEL = {1: "Pendente de pagamento", 2: "Pago", 3: "Cancelado", 4: "Cortesia"}
"statusLabel": _STATUS_LABEL.get(raw_status, "Desconhecido"),
```

**Regra no prompt** (`prompts/sbot_auth.py`): lista de campos técnicos proibidos nas respostas expandida para incluir `statusSubscription`, `accountReceiveId`, `activityScheduleId`, `subscriptionId`, `costCenterId`, `paymentPlanId`.

**Arquivos modificados:**
| Arquivo | Alterações |
|---|---|
| `tools/subscription.py` | `my_subscriptions` retorna `statusLabel` legível |
| `prompts/sbot_auth.py` | Regra de campos técnicos proibidos |

---

### Melhoria: domínio CBOT e outros eventos da SBOT ensinados ao agente

**Problema:** O agente não reconhecia "CBOT" como referência ao Congresso Brasileiro de Ortopedia e Traumatologia, tratando-o como termo desconhecido.

**Correção (`prompts/sbot_auth.py`):** Nova seção de conhecimento de domínio com mapeamento de abreviações recorrentes:
- **CBOT** → Congresso Brasileiro de Ortopedia e Traumatologia (evento anual numerado)
- **SGEE** → Simpósio de Gestão, Ensino e Especialização em Ortopedia
- **SBOT Lab** → cursos de laboratório e habilidades cirúrgicas
- **Módulos de Especialidade** → módulos científicos por subespecialidade
- **Anuidade SBOT** → taxa anual de associação

**Arquivo modificado:** `prompts/sbot_auth.py`

---

### Feature: status de associação do usuário (quite / não quite / não sócio)

**Contexto:** Usuários não associados (ex: pessoa 587, CPF 752.745.313-34) eram atendidos sem distinção de quem é sócio quite ou não sócio, causando confusão ao oferecer ou mencionar anuidades para quem nunca foi associado.

**Descoberta via API:** `GET /api/auth/persons/me` retorna:
- `data.association.financialStatus`: `"Q"` (quite), ausente (não sócio), outros (não quite)
- `data.association.financialStatusDescription`: `"Sócio Quite"`, `"Não socio"`, etc.
- `data.persona.id`: `1` = não associado, `4` = sócio quite (outros IDs = não quite)

**Novos campos no `SessionContext`** (`app/context.py`):
```python
persona_id: int | None        # 1=não associado, 4=quite, outros=não quite
member_status: str            # "Sócio Quite", "Não Sócio", etc.
is_member: bool               # False apenas para persona_id=1
```

**Extração no `get_me()`** (`app/webchat_agent.py`): após obter nome e hashLink, extrai `association` e `persona` do response e armazena no `SessionContext`.

**Guarda em `list_payment_plans`** (`tools/subscription.py`): para `is_member=False`, força `pendingMembershipFee=[]` independente do que a API retornar, e adiciona `memberNote` ao response instruindo o agente a não mencionar anuidades.

**Prompt** (`prompts/sbot_auth.py`): nova seção explicando os três estados:
- **Não Sócio**: nunca mencionar anuidades; preço NÃO SÓCIO é correto, não é erro
- **Sócio Quite**: fluxo normal; pode ter anuidades do próximo ciclo
- **Sócio Não Quite**: oferecer quitar anuidades para obter desconto de sócio

**Arquivos modificados:**
| Arquivo | Alterações |
|---|---|
| `app/context.py` | Campos `persona_id`, `member_status`, `is_member` |
| `app/webchat_agent.py` | Extração de `association` e `persona` do `get_me()` |
| `tools/subscription.py` | Guarda `pendingMembershipFee` para não sócios; `memberNote` no response |
| `prompts/sbot_auth.py` | Seção de status de associação |

---

### Feature: pagamento de anuidades sempre em conjunto (`pay_all_annuities`)

**Problema:** Ao listar anuidades via `my_subscriptions`, o agente podia oferecer ao usuário pagar uma ou algumas anuidades individualmente via `select_pending_payments` — o que não é o comportamento correto. Anuidades devem ser pagas todas juntas.

**Solução:**

**Novo método `ITargetClient.my_annuities()`** (`app/itarget_client.py`): bate no mesmo endpoint de `my_subscriptions` mas extrai exclusivamente o tab `"1"` (Anuidades), sem misturar com eventos ou cursos.

**Nova tool `pay_all_annuities`** (`tools/subscription.py`):
- Chama `api.my_annuities()` e filtra `statusSubscription=1`
- Se não houver pendências: retorna `no_pending_annuities` com mensagem clara
- Se houver: carrega **todos** os `accountReceiveId`s no contexto e retorna lista com descrição, vencimento e total somado
- Retorna `nextAction: call_list_payment_methods` — nunca permite seleção parcial

**`select_pending_payments` mantida** — docstring atualizada para deixar explícito que é exclusiva para **eventos**, nunca anuidades.

**Fluxo canônico no prompt** (`prompts/sbot_auth.py`):
1. Usuário menciona anuidades → chamar `pay_all_annuities`
2. Apresentar lista com total: "X anuidade(s) pendente(s) — pagas em conjunto. Deseja pagar agora?"
3. Com confirmação → `list_payment_methods`
4. **Proibido** `select_pending_payments` para anuidades; **proibido** oferecer escolha parcial

**Arquivos modificados:**
| Arquivo | Alterações |
|---|---|
| `app/itarget_client.py` | Novo método `my_annuities()` — extrai tab "1" da API |
| `tools/subscription.py` | Nova tool `pay_all_annuities`; docstring de `select_pending_payments` atualizada |
| `prompts/sbot_auth.py` | Seção 12 com fluxo canônico de anuidades; `pay_all_annuities` adicionada à lista de tools transacionais |

---

### Feature: log de pagamentos gerados com linkagem a webhooks (`payment_generated`)

**Motivação:** Não havia registro de quantos PIX/boletos eram gerados antes de um pagamento ser confirmado, nem qual pagamento gerado correspondia ao webhook recebido. Isso impossibilitava calcular taxa de conversão ou analisar volume de tentativas.

**Novo módulo `app/payment_generated_log.py`:**
- Tabela `payment_generated` no mesmo SQLite existente (`SESSION_DB_PATH`)
- Colunas: `id`, `generated_at`, `session_id`, `person_id`, `person_name`, `event_title`, `account_receive_ids` (JSON array), `method` (`pix`/`boleto`), `amount`, `gateway_ref` (60 chars do código gerado para auditoria), `status` (`pending`/`confirmed`), `confirmed_at`, `webhook_id` (FK para `payment_webhooks`)
- Índices em `person_id` e `status` para queries do dashboard
- **Design: sempre INSERT, nunca upsert** — preserva histórico de tentativas para análise de retry count

**Funções expostas:**
| Função | Descrição |
|---|---|
| `ensure_table()` | Cria tabela e índices idempotentemente — chamada no startup |
| `record(...)` | Insere uma linha para cada PIX ou boleto gerado; retorna o `id` do registro |
| `try_link(webhook_id, account_receive_id, person_id, amount)` | Liga webhook confirmado aos registros pendentes; dois níveis de match |
| `query(limit, status, person_id, method)` | Consulta filtrada para o dashboard |

**Estratégia de linkagem em `try_link`:**
1. **Primária (exata):** `account_receive_id` presente no JSON array via `json_each(account_receive_ids) WHERE value = ?`
2. **Fallback:** `person_id` + `ABS(amount - ?) < 0.01` — máximo 20 registros mais recentes

Todos os registros `pending` que fazem match são marcados `confirmed` — intencionalmente: se 3 PIX foram gerados, todos viram `confirmed`, mantendo o histórico de tentativas intacto.

**Hooks em `tools/payment.py`:** após cada geração bem-sucedida de PIX ou boleto, `pglog.record()` é chamado com `session_id=ctx.session_id`.

**Linkagem em `app/whatsapp_api.py`:** no handler de webhook de pagamento confirmado, após `pwlog.save()`, `pglog.try_link()` é chamado com o `accountReceiveId` extraído do body do webhook.

**`session_id` como diferenciador de canal:**
- `sbot-web-person-{id}` / `sbot-web-auth-{uuid}` → webchat
- `italo-wa-{numero}` → WhatsApp

O `session_id` já era o padrão do projeto e permite JOINs com a tabela `payment_webhooks`.

**`session_id` propagado para os agentes:**
- `app/webchat_agent.py`: `"session_id": session_id` adicionado ao `state` inicial do agente autenticado
- `app/agent_factory.py`: idem para o agente WhatsApp (Ítalo)
- `app/context.py`: propriedade `session_id` + setter adicionados ao `SessionContext`

**Arquivos modificados:**
| Arquivo | Alterações |
|---|---|
| `app/payment_generated_log.py` | **Novo arquivo** — tabela, `record`, `try_link`, `query` |
| `tools/payment.py` | `pglog.record()` após PIX e após boleto |
| `app/whatsapp_api.py` | `pglog.ensure_table()` no startup; `pglog.try_link()` no handler de webhook |
| `app/context.py` | Propriedade `session_id` adicionada |
| `app/webchat_agent.py` | `session_id` no state inicial do agente auth |
| `app/agent_factory.py` | `session_id` no state inicial do agente WABA |

---

### Fix: remoção de guarda de código que descartava `pendingMembershipFee` para não sócios

**Problema identificado:** a guarda `if not ctx.is_member: pending_membership = []` em `list_payment_plans` descartava o campo `pendingMembershipFee` retornado pela API para não sócios antes que chegasse ao LLM. O padrão do projeto é nunca descartar dados da API em código — o comportamento correto para não sócios é gerenciado pelo prompt via `memberNote`.

**Correção (`tools/subscription.py`):** guarda removida. O campo `pendingMembershipFee` passa integralmente ao LLM. O `memberNote` e o `nextActionHint` já instruem o agente a não mencionar anuidades para não sócios quando `is_member=False`.

**Arquivos modificados:**
| Arquivo | Alterações |
|---|---|
| `tools/subscription.py` | Remoção da guarda `if not ctx.is_member: pending_membership = []` |

---

### Feature: paridade WhatsApp — status de associação, domínio SBOT e anuidades

**Contexto:** os fixes de status de associação (persona_id/member_status), conhecimento de domínio (CBOT, SGEE) e fluxo de anuidades (`pay_all_annuities`) foram implementados na sessão anterior apenas para o webchat. O agente WhatsApp (Ítalo) ficou sem essas melhorias.

**1. Status de associação no WhatsApp (`tools/identity.py`):**

`_extract_and_store` — chamada pelos dois caminhos de autenticação (`identify_by_phone` e `identify_by_document`) — não extraía `persona.id` nem `association.financialStatusDescription` da resposta da API. O `SessionContext` nunca recebia esses campos para usuários WhatsApp, fazendo `is_member` sempre retornar `True` (default quando `persona_id=None`).

**Correção:** `_extract_and_store` agora extrai `persona.id` e `association.financialStatusDescription` quando presentes no response (disponíveis via `get_me()`, chamado por `_restore_or_get_me`), armazenando via `ctx.set_persona_id()` e `ctx.set_member_status()`.

**2. Domínio SBOT (`prompts/italo.py`):**

Nova seção adicionada com mapeamento de abreviações recorrentes:
- **CBOT** → Congresso Brasileiro de Ortopedia e Traumatologia
- **SGEE** → Simpósio de Gestão, Ensino e Especialização em Ortopedia
- **SBOT Lab** → cursos de laboratório e habilidades cirúrgicas
- **Módulos de Especialidade** → módulos científicos por subespecialidade
- **Anuidade SBOT** → taxa anual de associação

**3. Status de associação no prompt WhatsApp (`prompts/italo.py`):**

Nova seção com regras de comportamento por perfil:
- **Não sócio:** nunca mencionar anuidades ou desconto de sócio; preço exibido já é o correto
- **Sócio quite:** fluxo normal; `pendingMembershipFee` governa a oferta de anuidade
- **Sócio não quite:** proativamente mencionar que quitar anuidades garante desconto de sócio

**4. Anuidades sempre em conjunto no WhatsApp (`prompts/italo.py`):**

A tool `pay_all_annuities` já estava disponível (tools compartilhadas), mas `prompts/italo.py` não tinha as regras de uso. Adicionadas:
- Sempre chamar `pay_all_annuities` quando o usuário mencionar anuidades
- Proibido `select_pending_payments` para anuidades
- Proibido oferecer seleção parcial — anuidades são pagas todas juntas

**Arquivos modificados:**
| Arquivo | Alterações |
|---|---|
| `tools/identity.py` | `_extract_and_store` extrai `persona_id` e `member_status` do response |
| `prompts/italo.py` | Seções de domínio SBOT, status de associação e fluxo de anuidades |

---

## [Unreleased] — 2026-05-06

### Bug: agente em loop ao inscrever usuário sem categoria registrada

**Problema:** Ao tentar inscrever um usuário que não tinha categoria profissional cadastrada para o evento, o agente entrava em loop infinito e terminava dizendo "atualize seus dados na Área do Associado" em vez de apresentar a lista de categorias disponíveis.

**Causa:** `list_payment_plans` retornava 422 com `_domain_error` quando o endpoint `persons-payment-plans` identificava que o usuário não tinha categoria. O `nextAction` retornado era `call_get_cost_center_person`. O LLM então chamava `get_cost_center_person` → via `not_registered` com `nextAction: call_list_categories` — mas em vez de chamar `list_categories`, voltava a chamar `list_payment_plans`, gerando o mesmo 422. O ciclo se repetia até o modelo desistir.

**Diagnóstico:** Identificado via logs da sessão `sbot-web-auth-9893d78b` (evento 4802, cost center 190). Sequência observada:
1. `check_existing_subscription` → `not_subscribed` (correto)
2. `get_cost_center_person` → `link: []` → `not_registered` (correto)
3. `list_payment_plans` → 422 → `nextAction: call_get_cost_center_person` (**errado**)
4. `get_cost_center_person` → `not_registered` → `nextAction: call_list_categories` (correto)
5. `list_payment_plans` novamente → loop (modelo ignorava a instrução de chamar `list_categories`)

**Correção** (`tools/subscription.py` → `list_payment_plans` → bloco `domain_error`):
```python
# Antes:
"nextAction": "call_get_cost_center_person",
"nextActionHint": "A pessoa não tem categoria cadastrada. Chame get_cost_center_person AGORA."

# Depois:
"nextAction": "call_list_categories",
"nextActionHint": "A pessoa não tem categoria cadastrada. Chame list_categories AGORA e apresente a lista numerada ao cliente."
```

**Docstring corrigida** (`tools/category.py` → `get_cost_center_person`): removida referência obsoleta a "500 from API" — comportamento atual é `link: []` (200) para não registrado.

**Arquivos modificados:**
| Arquivo | Alterações |
|---|---|
| `sbot/tools/subscription.py` | `domain_error` agora aponta para `call_list_categories` |
| `sbot/tools/category.py` | Docstring atualizada |
| `main/tools/subscription.py` | Mesma correção aplicada |
| `main/tools/category.py` | Mesma correção aplicada |

---

## [Unreleased] — 2026-05-02

### Integração com `/subscribe/cart` — valor com desconto antes do pagamento

**Contexto:** O valor exibido ao usuário antes de escolher a forma de pagamento estava errado em dois cenários: (1) mostrava R$ 0,00 quando havia desconto e (2) não refletia o desconto de sócio aplicado pela plataforma. O campo `amountWithDiscount` não estava sendo utilizado em nenhum ponto do fluxo.

**Causa raiz:** Nenhuma tool buscava o endpoint `/api/subscription/persons/subscribe/cart` que é a única fonte do valor final consolidado (com desconto, evento + anuidades). As tools de pagamento usavam `payment.get("amount")` direto da resposta do PIX/boleto — que não inclui desconto.

**Novo método `get_subscription_cart()`** (`app/itarget_client.py`):
```python
GET /api/subscription/persons/subscribe/cart
params: changePaymentMethod=false, originInscription, accountReceiveIds[]

# Resposta mapeada para:
{
  "items": data["list"],          # lista de ar_ids vinculados (evento + anuidades)
  "summary": data["summary"]      # {total, totalWithDiscount, discount}
}
```

**Integração em `list_payment_methods`** (`tools/payment.py`):
- Chama `get_subscription_cart` com os `account_receive_ids` atuais
- Se os `cart_ar_ids` retornados diferem dos do contexto (ex: path `already_subscribed` só trouxe o ar_id do evento), atualiza o contexto e re-busca os métodos de pagamento com a lista completa
- Extrai `totalWithDiscount` e `discount` do `summary`; só inclui no response JSON se `totalWithDiscount > 0`
- `ctx.set_cart_amounts()` atualizado para persistir total, totalWithDiscount e discount na sessão
- Limpa valores anteriores (`set_cart_amounts(0,0,0)`) a cada chamada para evitar stale state de sessões anteriores

**Novos campos no `SessionContext`** (`app/context.py`):
```python
cart_total: float | None
cart_total_with_discount: float | None
cart_discount: float
set_cart_amounts(total, total_with_discount, discount)
```

**Hint no response `list_payment_methods`** — quando há desconto, o `nextActionHint` instrui o agente com o modelo exato de mensagem ao usuário:
> "Perfeito [nome], já garanti o seu desconto de R$ X e o valor final ficou R$ Y. Você deseja pagar no PIX, Boleto ou Cartão?"

**Arquivos modificados:**
| Arquivo | Alterações |
|---|---|
| `app/itarget_client.py` | Novo `get_subscription_cart()` |
| `app/context.py` | Campos `cart_total`, `cart_total_with_discount`, `cart_discount`, `set_cart_amounts()` |
| `tools/payment.py` | Integração do cart em `list_payment_methods`; amount_cents correto no PIX/boleto |
| `prompts/italo.py` | Regras de exibição do `totalWithDiscount` e `discount` |

---

### Bug: valor R$ 0,00 exibido ao usuário após chamada ao cart

**Problema:** Após implementar o cart, o agente dizia "o valor total é R$ 0,00".

**Causa:** O endpoint retorna `data.summary.totalWithDiscount`, mas o código tentava acessar `cart.get("totalWithDiscount")` diretamente no objeto `data` antes de desempacotar a chave `summary`. Resultado: `None` → `float(None)` → `0`.

**Correção:**
- `get_subscription_cart()` agora desempacota explicitamente: `return {"items": data["list"], "summary": data["summary"]}`
- `list_payment_methods` acessa `cart_data.get("summary", {}).get("totalWithDiscount")`
- `totalWithDiscount` e `discount` só são incluídos no response JSON se `cart_total_with_discount > 0` (nunca envia zero para o LLM)

---

### Bug: PIX gerado com valor R$ 1.400 em vez de R$ 900 (anuidade duplicada)

**Problema:** Com evento (R$ 400) + anuidade (R$ 500) = R$ 900 esperado. O PIX chegava com R$ 1.400.

**Causa:** `send_pix_order_details` recebe `amount_cents` (item base) e `extra_items` (lista de anuidades) e **soma** tudo internamente para montar o template `order_details`. Ao passar `cart_total_with_discount = 900` como `amount_cents` + anuidade R$ 500 como `extra_items`, o template somava 900 + 500 = 1.400.

**Correção** (`tools/payment.py` → `process_pix_payment`):
```python
if extra_items:
    # Passa só o valor do evento — o template soma event + cada extra_item
    amount_cents = int(ctx.event_amount * 100)
else:
    # Sem anuidade: usa o total com desconto diretamente
    amount_cents = int(float(ctx.cart_total_with_discount or payment.get("amount") or ctx.event_amount or 0) * 100)
```

**Boleto** (`process_bank_payment`): `amount_cents` usa `ctx.cart_total_with_discount or payment.get("amount")` — sem lógica de extra_items pois o template de boleto não tem linha por item.

---

### Bug: `link: []` interpretado como categoria registrada

**Problema:** Usuários sem categoria cadastrada no centro de custo de um evento caíam diretamente em `list_payment_plans`, que retornava erro de domínio. O fluxo de seleção de categoria nunca era acionado.

**Causa:** `get_cost_center_person` retornava `_registered: True` para qualquer resposta HTTP 200. A API retorna 200 com `{"data": {"link": []}}` (array vazio) quando a pessoa **não** tem categoria registrada. Só retornava 500 em casos extremos.

**Correção** (`app/itarget_client.py` → `get_cost_center_person`):
```python
body = resp.json()
data = body.get("data", body) if isinstance(body, dict) else body
link = data.get("link") if isinstance(data, dict) else None
registered = bool(link)  # [] → False → not_registered → fluxo correto
return {**body, "_registered": registered}
```

---

### Bug: path `already_subscribed` sem ar_ids das anuidades para pagamento

**Problema:** Usuário já inscrito com anuidade pendente chegava em `list_payment_methods` com apenas o ar_id do evento. A tentativa de pagamento falhava ou não incluía a anuidade.

**Causa:** `check_existing_subscription` só extraía os `accountReceiveId`s do campo `accountReceive` da inscrição — que contém apenas o ar_id do evento. Os ar_ids das anuidades vinculadas não estavam nesse response.

**Correção:** Resolvido como efeito colateral da integração do cart em `list_payment_methods`: ao comparar `cart_ar_ids` com `ctx.account_receive_ids`, detecta a diferença e atualiza o contexto com a lista completa antes de buscar os métodos de pagamento.

---

### `annuity_account_receive_ids` — captura antecipada dos ar_ids de anuidade

**Problema:** O `POST /api/subscription/persons/subscribe` retorna apenas o ar_id da nova inscrição de evento, não retorna os ar_ids das anuidades pré-existentes. Sem eles, não era possível montar o request de pagamento completo.

**Causa:** Os ar_ids das anuidades só estão disponíveis no response de `list_payment_plans` (campo `pendingMembershipFee[].accountReceiveId`). Após `create_subscription`, essa informação não é mais retornada pela API.

**Correção** (`tools/subscription.py` → `list_payment_plans`):
```python
annuity_ar_ids = [
    int(a["accountReceiveId"])
    for a in pending_membership
    if a.get("accountReceiveId")
]
ctx.set_annuity_account_receive_ids(annuity_ar_ids)
```

**Uso em `create_subscription`:**
```python
if include_annuity:
    for ar_id in ctx.annuity_account_receive_ids:
        if ar_id not in account_ids:
            account_ids.append(ar_id)
ctx.set_account_receive_ids(account_ids)  # evento + anuidades
```

**Novo campo no `SessionContext`** (`app/context.py`):
```python
annuity_account_receive_ids: list[int]
set_annuity_account_receive_ids(ids: list[int])
```

---

### Prompt: regras de exibição do desconto e do `totalWithDiscount`

**Problema:** Mesmo após o cart retornar o desconto corretamente, o agente às vezes mencionava o valor bruto ou inventava desconto quando não havia.

**Regras adicionadas** (`prompts/italo.py`):
- Usar **sempre** `totalWithDiscount` como valor final — nunca recalcular somando campos manualmente
- Se `discount > 0`: modelo exato de mensagem com desconto
- Se `discount == 0` ou ausente: mensagem sem mencionar desconto
- NUNCA mencionar valor bruto quando há desconto
- NUNCA inventar desconto se `discount` for 0 ou ausente
- NUNCA chamar `select_pending_payments` automaticamente após `my_subscriptions` — só se o usuário pedir explicitamente

---

### Tratamento de erros robusto em todas as tools

**Problema:** Tools sem tratamento de 403 deixavam a sessão quebrada. Tools sem `error_log` não registravam erros para análise posterior.

**Correções em `tools/subscription.py` e `tools/payment.py`:**
- Todos os blocos `except` agora verificam `httpx.HTTPStatusError` com status 403 → limpa o token e retorna `status: token_expired` com instrução para re-autenticar
- Todos os erros não-403 chamam `error_log.record()` com contexto (ar_ids, plan_id, etc.)
- `my_subscriptions`: response de lista vazia agora inclui `nextActionHint` para o agente não chamar `select_pending_payments` automaticamente e aguardar o usuário escolher o que pagar
- `create_subscription`: detecta duplicata de inscrição por keywords no erro e retorna `status: already_subscribed` com hint para chamar `check_existing_subscription`

---

### `app/error_log.py` — adicionado ao sbot

**Problema:** `sbot` não tinha o módulo `error_log` (existia apenas em `main`), mas as tools em `sbot` passaram a importá-lo após a sincronização.

**Ação:**
- Copiado `app/error_log.py` de `main` para `sbot/app/`
- `app/whatsapp_api.py` (sbot): adicionado `import app.error_log as error_log` e `error_log.init(SESSION_DB_PATH)` no hook `startup`

**Tabela criada:** `tool_errors` no SQLite com campos `tool_name`, `error_type`, `http_status`, `error_message`, `request_context`, `raw_traceback`, `eval_requested`.

---

## [Unreleased] — 2026-04-28

### Busca de evento por nome — liberação do carrossel

**Problema:** Usuário `558588041498` no domingo (26/04) disse *"Quero fazer a inscrição"* e *"E este de inovação e tendências na gestão digital"*, mas o agente não conseguia mapear o nome do evento para o ID. Resultado: ficou forçando o usuário a clicar no carrossel ("toque no botão dele no carrossel acima"), criando uma experiência ruim quando o usuário preferia digitar o nome.

**Causa:** O agente só tinha duas tools de evento:
- `list_events` — envia carrossel interativo e salva cache em `available_events`
- `event_detail(activity_schedule_id)` — exige o ID numérico

Não havia mecanismo para converter um nome de evento mencionado pelo usuário em um `activity_schedule_id`.

**Correção:**

**Nova tool `find_event_by_name`** (`tools/events.py`):
```python
@tool
async def find_event_by_name(event_name: str) -> str:
    # Busca em ctx.available_events (cache do session_state)
    # Match exato primeiro, depois parcial (substrings e palavras > 3 chars)
    # Retorna activity_schedule_id + nextActionHint para chamar event_detail
```

**Regras novas no prompt** (`prompts/italo.py`):
- Quando o usuário mencionar o nome de um evento, **NUNCA** diga para tocar no carrossel ou botão
- Chame `find_event_by_name` com o nome mencionado
- Se encontrar, chame `event_detail` imediatamente e continue o fluxo de inscrição
- Se não encontrar (`not_found`), peça para verificar o nome ou listar novamente

**Arquivos modificados:**
| Arquivo | Alterações |
|---|---|
| `tools/events.py` | Nova tool `find_event_by_name`; match exato e parcial em `available_events` |
| `prompts/italo.py` | Instrução explícita para usar `find_event_by_name` ao invés de forçar carrossel |

---

## [Unreleased] — 2026-04-23

### Mensagens interativas WhatsApp para PIX e Boleto

**Contexto:** Análise do fluxo n8n equivalente revelou que o iTarget envia mensagens interativas (`order_details`) para PIX e boleto, com QR code, linha digitável e PDF — muito mais rico que uma mensagem de texto simples.

**PIX** (`tools/payment.py` → `process_pix_payment`):
- Chama `waba_client.send_pix_order_details()` com `pix_dynamic_code`
- Retorna `sent_via_whatsapp: true` para o agente não repetir o código em texto

**Boleto** (`tools/payment.py` → `process_bank_payment`):
- Extrai `typeable_barcode` de `charges[0].last_transaction.gateway_response_fields.typeable_barcode` (o campo top-level `digitableLine` sempre chega vazio)
- Extrai `print_url` de `charges[0].print_url`
- Busca PDF via HTML scraping do endpoint de impressão: regex `window.location = "...pdf"` (mesmo padrão do n8n)
- Envia PDF como documento WhatsApp (`send_boleto_document`) + mensagem interativa `order_details` com linha digitável (`send_boleto_order_details`)
- `asyncio.sleep(3)` entre documento e interativa para respeitar ordering do WhatsApp

**`print_token` extraído da URL** — o campo `printToken` estava sempre vazio; o token real vem na query string `?token=...` do campo `url`.

**Novos métodos `WABAClient`** (`app/waba_client.py`):
- `send_pix_order_details(to, pix_copy_paste, amount_cents, event_title, merchant_name, pix_key, pix_key_type, extra_items)`
- `send_boleto_document(to, pdf_url)`
- `send_boleto_order_details(to, digitable_line, amount_cents, event_title)`

---

### Detecção de rejeição Vindi dentro de resposta 201

**Problema:** `POST /api/payments/bank/process` retornava HTTP 201 mas o pagamento havia sido rejeitado (endereço incompleto, dados inválidos).

**Causa:** O iTarget retorna 201 mesmo quando o Vindi rejeita. O status real está em `data.gatewayResponse.charges[0].last_transaction.status == "rejected"` com a mensagem em `gateway_message`.

**Correção** (`app/itarget_client.py` → `process_bank_payment`):
```python
if last_tx.get("status") == "rejected":
    raise ValueError(last_tx.get("gateway_message") or "Pagamento rejeitado pelo gateway.")
```

---

### Webhook de confirmação de pagamento externo

**Novo endpoint:** `POST /webhooks/payment-confirmed`

Recebe confirmação de pagamento do sistema iTarget e envia mensagem WhatsApp ao usuário.

**Campos aceitos** (`PaymentConfirmedPayload`):
- `phone` (obrigatório), `person_name`, `event_name`, `amount`, `payment_method` (pix/boleto/cartao/credit_card), `due_date`, `installments`, `extra`

**Autenticação:** header `X-Webhook-Secret` validado contra `PAYMENT_WEBHOOK_SECRET` env var.

**Normalização de telefone** (`_normalize_phone`):
- Remove não-dígitos
- Remove zeros à esquerda (`re.sub(r"^0+", "")`) — corrige `085996635554` e `0085996635554`
- Adiciona `55` se ausente

**Mensagem gerada** (`_build_confirmation_message`): contextual por método, nome do evento em Title Case, valor formatado R$ X.XXX,XX.

---

### Persistência de webhooks de pagamento

**Novo arquivo:** `app/payment_webhook_log.py`

Tabela SQLite `payment_webhooks`: `id, received_at, phone_raw, phone_norm, payload (JSON), status, error, wa_response`.
- `status`: `sent` | `error` | `auth_failed` | `invalid_phone`
- Todos os webhooks recebidos são persistidos, independente do resultado

**Novo endpoint admin:** `GET /admin/payment-webhooks?limit=100&status=...`

---

### Tom e personalidade do agente

**Problema:** Agente muito seco e robotizado para o perfil do cliente (associações, órgãos, entidades profissionais).

**Correções** (`prompts/italo.py`):

**Português correto sem gírias:**
- Proibido explicitamente: `tá, pra, tô, cê, vc, tbm, blz, tranquilo, rapidinho, fechou, beleza`
- Obrigatório: `está, para, estou, você, também, tudo bem, sem problemas`

**Emojis contextuais (não decorativos):**
- 👋 só na primeira saudação; 🎉 ao confirmar inscrição; ✅ confirmações importantes; 📌 listas; 📅 datas; 💰/💳 valores; 📄 documentos
- Proibidos como enfeite: 😊, 😉, 🙂, ✨, 💡, 🚀 em frases genéricas
- Padrão é ZERO emoji — muitas respostas corretas não terão nenhum

**Regra absoluta de saudação:**
- Cumprimento único na primeira mensagem com 👋
- Nunca reiniciar com "Oi", "Olá", "Ei" em mensagens subsequentes
- Não repetir firstName no início de cada resposta

---

### Guard: `create_subscription` sem `payment_plan_id`

**Problema:** Roberta recebeu erro 400 Bad Request com `paymentPlanId: [null]`.

**Causa:** O agente chamou `create_subscription` sem ter chamado `list_payment_plans` antes (ou após `no_plan`), então `ctx.payment_plan_id` era `None` → `[None]` no body HTTP.

**Correções:**
- `tools/subscription.py`: retorna erro descritivo com `nextAction: call_list_payment_plans` se `ctx.payment_plan_id` for `None`
- `app/itarget_client.py`: `ValueError` se `payment_plan_id` for falsy antes de montar o body
- `prompts/italo.py`: instrução explícita — nunca chamar `create_subscription` sem `list_payment_plans` antes; se `list_payment_plans` retornar `no_plan`, não inventar valor nem confirmar inscrição

---

### `my_subscriptions` — Parser para formato array-de-arrays

**Problema:** Descoberto ao investigar a Roberta: `tabs.data` pode vir como `[[...], [...]]` (lista de listas por tab) em vez do formato dict `{"0": [...], "1": [...]}`.

**Antes:** código retornava apenas `tabs_data[0]` — ignorava todos os outros tabs.

**Correção** (`app/itarget_client.py`): quando `tabs_data` é lista, itera e achata todos os sub-arrays com deduplicação por `accountReceiveId`.

**Nota diagnóstica:** A Roberta genuinamente não tinha inscrições pendentes na API no momento — confirmado com token real via curl. `[[]]` = API retornou um tab vazio. Provavelmente anuidade existe no cadastro mas sem `accountReceiveId` (não faturada).

**Melhoria:** `my_subscriptions` agora inclui `hint` quando retorna vazio, orientando o agente a não afirmar "tudo em dia" e direcionar o usuário ao suporte caso insista que tem pendências.

---

## [Unreleased] — 2026-04-22

### Autenticação por Telefone

**Problema:** `POST /api/auth/persons/automation` retornava 404 para o número do Bruno em todos os testes.

**Causa:** WhatsApp envia números brasileiros de 8 dígitos no formato antigo sem o 9º dígito móvel (`558597481913`, 12 dígitos). O iTarget armazena com o 9º dígito após o DDD (`5585997481913`, 13 dígitos). O normalizador original tirava apenas o `55` e enviava `8597481913`, que não existe no cadastro.

**Formatos testados:**
| Formato | Status |
|---|---|
| `8597481913` | 404 |
| `85997481913` | 404 |
| `558597481913` | 404 |
| `+558597481913` | 404 |
| `5585997481913` | ✅ 200 |

**Correção** (`tools/identity.py` → `_normalize_phone`):
- Números de 12 dígitos (55 + DDD + 8 dígitos): insere `9` após o DDD → 13 dígitos
- Números de 13 dígitos: já estão no formato correto, retorna como está

---

### Persistência de Sessão (Agno session state)

**Problema:** Após cada mensagem, alterações feitas pelas tools (token, person_id, account_receive_ids, etc.) não eram salvas no SQLite — na próxima mensagem o estado estava zerado.

**Causa:** `agent.arun()` era chamado sem o parâmetro `session_state`. O Agno criava internamente um dict vazio para `run_context.session_state`, divergente do `ctx._s` (que as tools escreviam). O `cleanup_and_store` salvava o dict vazio, não o dict com os dados.

**Correção** (`app/whatsapp_bridge.py`):
```python
result = await agent.arun(msg.text, session_state=agent.session_state)
```

---

### Auto-restore de Token após Restart

**Problema:** Após reiniciar o serviço, o `ITargetClient` era instanciado sem token. O LLM chamava outras tools sem re-autenticar, resultando em `RuntimeError: Not authenticated`.

**Correção** (`app/itarget_client.py`):
- `ITargetClient.__init__` agora aceita `ctx: SessionContext | None`
- `_auth_headers()` tenta restaurar o token de `ctx.access_token` caso `self._access_token` esteja vazio
- Token é cacheado em `self._access_token` após a primeira restauração

---

### Endpoint de Registro de Categoria

**Problema:** `POST /api/register/persons/register-category` retornava 405 Method Not Allowed no ambiente demo.

**Investigação:** Testados `POST`, `PUT`, `PATCH` e variações de path. Apenas `POST /api/register/persons/categories` com o mesmo body retorna 201.

**Correção** (`app/itarget_client.py` → `register_category`):
```python
# Antes (405):
POST /api/register/persons/register-category

# Depois (201):
POST /api/register/persons/categories
```
Body: `{costCenterId, costCenterCategoryProfessionalId}`

---

### `check_existing_subscription` — Resposta null tratada como inscrito

**Problema:** A API retornava `{"data": null}` quando não havia inscrição. O código anterior não desempacotava o envelope `{"data": ...}` e interpretava o objeto inteiro como inscrição existente.

**Correção** (`app/itarget_client.py` → `check_existing_subscription`):
- Desempacota envelope `{"data": ...}`
- Retorna `None` se data for null, vazio ou ausente
- Status 422 e 500 também retornam `None` (não inscrito)

---

### `create_subscription` — Campo errado no body

**Problema:** `POST /api/subscription/persons/subscribe` retornava 500 com "planos são obrigatórios".

**Causa:** O body enviava `paymentPlanIds` (plural). A API exige `paymentPlanId` (singular) como array.

**Correção** (`app/itarget_client.py` → `create_subscription`):
```python
# Antes (500):
{"paymentPlanIds": [plan_id]}

# Depois (201):
{"paymentPlanId": [plan_id], "originInscription": 2}
```

---

### `list_payment_methods` — Parâmetro obrigatório faltando

**Problema:** `GET /api/subscription/payments/methods` retornava 422 "centro de custo é obrigatório".

**Causa:** O endpoint exige `costCenterId` E `accountReceiveIds[]` (notação de array). A implementação anterior enviava apenas os IDs sem o `costCenterId` e sem a notação de colchetes.

**Correção** (`app/itarget_client.py` → `list_payment_methods`):
```python
params = [("costCenterId", cost_center_id)]
for aid in account_receive_ids:
    params.append(("accountReceiveIds[]", aid))
```

---

### `process_pix_payment` — Campo errado para expiração

**Problema:** `POST /api/payments/pix/process` retornava 400.

**Causa:** O body enviava `expiresAt` com uma string ISO. A API exige `expiresIn` com um inteiro em segundos.

**Correção** (`app/itarget_client.py` → `process_pix_payment`):
```python
# Antes (400):
{"expiresAt": "2026-04-22T21:00:00Z"}

# Depois:
{"expiresIn": 3600}
```

---

### `my_subscriptions` — Parser duplo envelope + tabs por string

**Problema:** A tool `my_subscriptions` retornava lista vazia. Nenhuma inscrição aparecia.

**Causa:** A API retorna `{"data": {"tabs": {"data": {"3": [...]}}}}` — dois níveis de envelope. O parser anterior tentava acessar a chave `"0"` (todas as inscrições), mas a API só retorna as abas com dados (para o Bruno, apenas a aba `"3"` — Cursos). O parser externo `{"data": ...}` também não estava sendo desempacotado.

**Correção** (`app/itarget_client.py` → `my_subscriptions`):
- Desempacota envelope externo `{"data": ...}` se o valor não for lista
- Itera sobre todos os valores do dict `tabs.data` (não assume chave `"0"`)
- Deduplica por `accountReceiveId`

---

### Pagamento com Cartão — URL não enviada

**Problema:** Quando o usuário escolhia Cartão, o agente respondia "Para cartão, finalize pelo site iTarget" sem enviar a URL.

**Causa:** A tool `list_payment_methods` retornava `"Cartão (site)"` nos labels mas não incluía a URL no response. O LLM inventava instruções genéricas.

**Correção** (`tools/payment.py` → `list_payment_methods`):
- Sempre inclui `checkoutUrl` (gerado por `ctx.card_payment_url()`) no response
- `nextActionHint` instrui explicitamente: "Se o usuário escolher Cartão, envie o checkoutUrl diretamente"
- Se apenas cartão disponível: retorna `status: redirect_to_site` com URL imediata

**URL do cartão** (`app/context.py` → `card_payment_url`):
```
https://{client}.hub{env}.itarget.com.br/offer/login?hash={hashLink}&redirect=base64(/cartCheckout?items=base64(id1,id2))
```

---

### Pagamento múltiplo — URL com apenas um evento

**Problema:** Ao pedir para pagar dois eventos com cartão, a URL gerada continha apenas um `accountReceiveId`.

**Causa:** `ctx.account_receive_ids` só tinha o ID do evento mais recente. Não havia mecanismo para agregar IDs de múltiplos eventos pendentes.

**Correção:**
- Nova tool `select_pending_payments` (`tools/subscription.py`): recebe lista de `activityScheduleIds`, busca os `accountReceiveId`s correspondentes em `my_subscriptions`, armazena todos em `ctx`
- `my_subscriptions` agora expõe `accountReceiveId` (singular) por item no summary
- `card_payment_url` codifica todos os IDs como `id1,id2` em base64

---

### Cancelamento de Inscrição

**Endpoint descoberto:** `POST /api/subscription/persons/unsubscribe`
```json
{"subscriptionIds": [int]}
```
- 200: cancelamento processado
- 422: erro de negócio (não é possível cancelar)

**Implementação:**
- `app/itarget_client.py` → `cancel_subscription`
- `tools/subscription.py` → tool `cancel_subscription`
- `my_subscriptions` agora expõe `subscriptionId` (campo `id` da API) em cada item
- Prompt atualizado: chama `my_subscriptions` para obter `subscriptionId`, confirma evento, chama `cancel_subscription`

---

### Formatação WhatsApp — Title Case e layout

**Problema:** Nomes de eventos chegavam em CAIXA ALTA da API (`INOVAÇÃO E TENDÊNCIAS NA GESTÃO DIGITAL`), datas sem padrão, IDs numéricos expostos ao usuário.

**Correção:**

**`_title_case()` helper** (`tools/events.py`):
- Converte strings com ≥70% maiúsculas para Title Case
- Preserva strings já em mixed case
- Respeita conectivos portugueses: `e, de, da, do, em, para, com, a, o, na, no...`
- Exemplos:
  - `INOVAÇÃO E TENDÊNCIAS` → `Inovação e Tendências`
  - `ANUIDADE 2025` → `Anuidade 2025`
  - `Inovação Digital` → `Inovação Digital` (inalterado)

**Aplicado em:**
- `tools/events.py` → títulos de eventos em `list_events`
- `tools/subscription.py` → descrições em `my_subscriptions`
- `tools/category.py` → nomes de categorias em `list_categories`

**Regras de formatação no prompt** (`prompts/italo.py`):
- Nunca exibe IDs numéricos ao usuário
- Listas de eventos: `*Nome* — _DD/MM/AAAA_ — R$ X.XXX,XX`
- Inscrições pendentes: `📌 *Nome* — R$ X.XXX,XX — Vence DD/MM/AAAA`
- Máximo 3 linhas por parágrafo
- Valores: `R$ X.XXX,XX` (vírgula decimal, ponto milhar)
- Datas: `DD/MM/AAAA`
- Usa sintaxe WhatsApp: `*negrito*`, `_itálico_` — nunca `**` ou `__`

---

### Problema em aberto — PIX e Boleto no ambiente demo

**Situação:** `POST /api/payments/pix/process` e `POST /api/payments/bank/process` retornam 400 para o usuário Bruno.

**Causa raiz:** Antes de emitir PIX ou boleto, o iTarget chama `PUT https://app.vindi.com.br/api/v1/customers/113675166` para atualizar o cadastro do cliente no Vindi. O Vindi rejeita com 422 no campo `phones.number: inválido(a)`.

**Não é bug de código** — é dado do cadastro do Bruno no ambiente demo (telefone em formato inválido para o Vindi). O pagamento via Cartão funciona normalmente.

**Solução:** Corrigir o telefone do Bruno no painel admin do iTarget para um formato aceito pelo Vindi.

---

## Arquivos modificados

| Arquivo | Alterações |
|---|---|
| `app/itarget_client.py` | Todos os endpoints corrigidos; auto-restore de token; `cancel_subscription`; parser `my_subscriptions` (dict e array); detecção rejeição Vindi 201; guard `payment_plan_id` nulo; logging raw response |
| `app/whatsapp_bridge.py` | `session_state` passado explicitamente para `arun()` |
| `app/waba_client.py` | `send_pix_order_details`; `send_boleto_document`; `send_boleto_order_details` |
| `app/whatsapp_api.py` | Webhook `/webhooks/payment-confirmed`; `_normalize_phone`; `_build_confirmation_message`; integração `payment_webhook_log` |
| `app/payment_webhook_log.py` | *(novo)* Tabela `payment_webhooks`; `ensure_table`, `save`, `query` |
| `app/agent_factory.py` | `ctx` passado para `ITargetClient`; `ITARGET_CLIENT`/`ITARGET_ENV` do config; `waba_client` passado para `build_payment_tools` |
| `app/context.py` | Fallback para config em `client` e `env`; `card_payment_url` com múltiplos IDs; `event_title`, `event_amount`, `pending_membership_details` |
| `app/admin_router.py` | Endpoint `GET /admin/payment-webhooks` |
| `app/config.py` | `PIX_MERCHANT_NAME`, `PIX_KEY`, `PIX_KEY_TYPE`, `PAYMENT_WEBHOOK_SECRET` |
| `tools/identity.py` | `_normalize_phone` com inserção do 9º dígito |
| `tools/events.py` | `_title_case` helper; aplicado nos títulos; salva `event_title` no contexto |
| `tools/subscription.py` | `cancel_subscription`; `select_pending_payments`; `subscriptionId` no summary; `_title_case`; guard `payment_plan_id`; hint quando `my_subscriptions` vazio |
| `tools/category.py` | `_title_case` nos nomes de categorias |
| `tools/payment.py` | PIX interativo; boleto com PDF + interativa; extração `typeable_barcode` e `print_token`; `checkoutUrl`; `expiresIn`; `_extract_pdf_from_html` |
| `prompts/italo.py` | Tom humano; português correto; emojis contextuais; regra anti-re-saudação; guard `no_plan`; guard `create_subscription` sem plan; fluxo de cancelamento; multi-pagamento |
