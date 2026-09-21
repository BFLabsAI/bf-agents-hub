# Port Registry & Service Mapping (Example)

Este arquivo serve como **modelo de referência** para organizar as portas, slugs, serviços systemd e rotas públicas dos agentes na sua VPS.

> **Regra de Produção:** Copie este arquivo para `PORTS.md` e adicione cada nova instância de cliente. O arquivo `PORTS.md` real é ignorado pelo Git para não expor a infraestrutura dos clientes.

---

## Tabela de Portas (Faixa 7770 – 7799)

| Porta | Cliente / Instância | Slug (`SLUG`) | Tipo | URL Pública (Webhook) | Serviço Systemd |
|---|---|---|---|---|---|
| `7771` | Exemplo Suporte Interno | `suporte` | WABA (Oficial) | `https://agentshub.seudominio.com.br/suporte/webhook` | `agno-suporte.service` |
| `7772` | Exemplo SDR Vendas A | `sdr-alpha` | UazAPI (SDR) | `https://agentshub.seudominio.com.br/sdr-alpha/webhook` | `agno-sdr-alpha.service` |
| `7773` | Exemplo SDR Vendas B | `sdr-beta` | UazAPI (SDR) | `https://agentshub.seudominio.com.br/sdr-beta/webhook` | `agno-sdr-beta.service` |
| `7774` | Exemplo Concierge Eventos | `eventos` | WABA (Oficial) | `https://agentshub.seudominio.com.br/eventos/webhook` | `agno-eventos.service` |
| `7775` | *Próximo cliente* | `{slug}` | *Template* | `https://agentshub.seudominio.com.br/{slug}/webhook` | `agno-{slug}.service` |

---

## Procedimento de Alocação de Nova Porta

1. **Checar portas em uso antes de subir:**
   ```bash
   ss -tlnp | grep -E '77[7-9][0-9]'
   ```
2. **Escolher a menor porta disponível** na faixa sequencial (ex: 7775, 7776...).
3. **Registrar a porta** no `PORTS.md` local.
4. **Configurar a porta no `.env`** do cliente (`PORT=7775`).
5. **Configurar rota no Cloudflare Tunnel** (em `~/.cloudflared/bf-os.yml`).
6. **Criar e iniciar o systemd service** (`agno-{slug}.service`).
