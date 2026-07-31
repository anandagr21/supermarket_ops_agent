# Supermarket Ops Agent

Telegram bot that runs an Indian kirana store end-to-end. The owner operates the entire shop from a chat window — receiving stock, cutting bills, managing credit, closing the day, pulling invoices and analysis decks.

**Bot:** [@Anandmart_bot](https://t.me/Anandmart_bot)

**Run:** `uvicorn app.main:app --reload --port 8000` + `ngrok http 8000` + set Telegram webhook.

**Deployed on:** [Render](https://supermarket-ops-agent-els4.onrender.com) with PostgreSQL.

## Harness: `deepagents` with gpt-4o-mini

Multi-agent architecture using `create_deep_agent` with four domain subagents (inventory, billing, khata, preferences). Each subagent gets only its own tools — domain isolation prevents tool confusion. gpt-4o-mini handles the framework's injected middleware gracefully at ~6s per request.

Filesystem access is denied globally via `FilesystemPermission(deny /**)`, since all data lives in PostgreSQL.

**Why subagents over a flat agent?** Subagents scope tools tightly and keep system prompts focused. The model delegates by picking the right subagent via the `task` tool — this is agent-first routing, not hardcoded branches. DeepSeek v4-flash struggled with the framework's injected tool schemas; gpt-4o-mini ignores them and routes correctly.

**Loop prevention:** `_ToolCallGuardMiddleware` (hard stop after 20 tool messages) + `recursion_limit=25` on LangGraph config. Both cap runaway loops caused by the model retrying failed tool calls.

## Control loop

```
Telegram webhook → main.py
  → idempotency check (ProcessedUpdate by update_id)
  → voice detection → Whisper transcription (if voice message)
  → StoreAgentOrchestrator → fetches preferences from DB
    → orchestrator LLM routes to subagent
      → subagent calls domain tool → validator LLM → service → repository → Postgres
  → sends response (with inline keyboard if payment selection requested)
  → sends generated file (PDF/PPTX) if queued via PENDING_FILES
```

## Tools (capabilities from spec §3)

| Domain | Tools |
|---|---|
| **Inventory** | `add_product`, `update_product`, `receive_stock`, `query_stock`, `list_products` |
| **Billing** | `add_to_bill`, `remove_from_bill`, `update_bill_item`, `view_draft_bill`, `finalize_bill`, `query_todays_sales`, `generate_invoice`, `generate_analysis_deck` |
| **Khata** | `open_khata_account`, `increase_customer_debt`, `record_khata_payment`, `get_khata_balance`, `list_khata_customers` |
| **Preferences** | `set_preference`, `get_preferences`, `new_chat` |
| **Voice** | Telegram voice → Whisper API → transcribed text → agent pipeline |

## How each hard part is solved

### 1. Grounding
Three-layer defense: **Pydantic schema** enforces `mrp` is required and >0 (rejects before tool fires); **prompt** tells the model to ask for missing cost/MRP/GST before calling tools; **validator LLM** cross-references financial tool args against recent user chat history and rejects hallucinated numbers. Prices, GST, and stock come from the DB — the model is told "never invent prices or stock levels." `query_stock` now includes GST percentage in its response so the model never guesses tax rates.

### 2. Oversell guard
`billing_service.finalize_bill()` checks `stock_quantity < item.quantity` per line item before decrementing. Entire bill rolls back if any SKU is short. This is enforced at the service layer, not the prompt.

### 3. GST correctness
MRP is tax-inclusive. Base price is back-calculated: `base = total / (1 + rate/100)`. CGST and SGST are computed independently from the base using half-rate — this guarantees they are always equal (intra-state). Rounding is `round()` per item, stored on `BillItem` at finalize time (snapshot principle). Default GST is 0% if not specified.

### 4. Multi-turn bills
`add_to_bill` creates a draft Bill on first call and appends sequentially (prompt-level serialization prevents parallel tool races). `update_bill_item` sets quantity (not adds). `remove_from_bill` drops items. `update_product` changes price/GST without touching stock. Stock decrements only at `finalize_bill`. `get_or_create_draft_bill` retries on commit race.

### 5. Idempotency
Telegram's `update_id` is the idempotency key at the webhook entry point. `ProcessedUpdate` table caches replies before the orchestrator runs. If the same `update_id` arrives again (retry), the cached reply returns immediately. Race condition on concurrent retries is handled with try/except on the INSERT.

### 6. Concurrency
`SELECT ... FOR UPDATE` with `with_for_update()` locks product rows during `finalize_bill`. A concurrent bill on the same SKU blocks and reads the updated stock. The oversell guard becomes atomic across transactions.

### 7. Guardrails
- **Sell below cost**: `add_product`, `update_product`, and `receive_stock` reject MRP < cost_price
- **Don't delete stock**: no delete-stock tool exists; stock only decreases via `finalize_bill` with oversell guard
- **Don't settle nonexistent khata**: `record_khata_payment` returns error if account doesn't exist
- **Stock precision**: all stock writes use `round(quantity, 2)` to prevent float artifacts
- **Integer out of range**: all `chat_id` columns use PostgreSQL `BIGINT` to accommodate Telegram's large chat IDs

### 8. Real artifacts
- **PDF invoices**: fpdf2 generates GST-compliant invoices with shop name/GSTIN/address from preferences, per-item HSN, CGST/SGST breakup, totals. Sent as Telegram document via `sendDocument` API. Bill uses UUID for public-facing ID.
- **PPTX analysis deck**: python-pptx generates 6-slide deck — title, KPI dashboard, pie chart (payment modes), bar charts (top products by qty + revenue), stock health table. Real native PowerPoint charts with embedded data, not screenshots.

### 9. Memory across sessions
`OwnerPreference` table persists preferences in PostgreSQL. Every webhook call instantiates a fresh orchestrator that reads preferences from DB and injects them into the system prompt of every subagent. `/new chat` bumps the LangGraph thread ID — conversation history resets, all business data and preferences survive.

## Stretch features

- **Voice-note orders**: Telegram voice messages transcribed via OpenAI Whisper, fed into the billing pipeline
- **Branded invoices**: shop_name, gstin, shop_address read from `OwnerPreference` table; set via "set shop name to X"
- **int display for whole units**: packet/dozen/piece units display as integers (240 packets, not 240.0)
- **Payment mode buttons**: `request_payment_mode` tool queues an inline keyboard with Cash/UPI/Card/Khata buttons
- **UUID bill IDs**: public-facing bill identifiers (`uuid[:8]`) instead of predictable auto-increment integers
