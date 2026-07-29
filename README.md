# Supermarket Ops Agent

Telegram bot that runs an Indian kirana store end-to-end. The owner operates the entire shop from a chat window — receiving stock, cutting bills, managing credit, closing the day, and pulling reports.

**Bot:** [@Anandmart_bot](https://t.me/Anandmart_bot)

Instructions to run: `uvicorn app.main:app --reload` then expose port 8000 via ngrok/localtunnel and set the Telegram webhook.

## Harness: `deepagents` (LangGraph-based)

I chose `deepagents` because it provides subagent delegation out of the box. The orchestrator delegates to three specialized subagents (inventory, billing, khata) rather than one monolithic agent — this keeps each subagent's system prompt focused and reduces hallucination by scoping tools tightly. The framework handles tool schema binding, conversation memory via LangGraph checkpoints, and structured output validation through Pydantic.

## Control loop

```
Telegram webhook → main.py
  → idempotency check (ProcessedUpdate table by update_id)
  → StoreAgentOrchestrator.handle_message()
    → deepagents orchestrator LLM
      → delegates to inventory_agent / billing_agent / khata_agent
        → tool call → validator LLM (guardrail) → service → repository → DB
      → returns reply → cached in ProcessedUpdate
  → sendMessage back to Telegram
```

## Skill / tool design

Three domains, each with repository → service → tool layers:

| Domain | Tools | State |
|---|---|---|
| **Inventory** | `add_product`, `receive_stock`, `query_stock` | Products, stock quantities, cost/MRP |
| **Billing** | `add_to_bill`, `finalize_bill`, `query_todays_sales` | Draft bills, BillItems, finalized transactions |
| **Khata** | `open_khata_account`, `record_khata_payment`, `get_khata_balance` | Customer credit accounts, transaction log |

Each tool call passes through a validator LLM (in `agent.py:_wrap`) that cross-references the tool arguments against the user's chat history before executing. If the model hallucinates a price or GST rate the user never mentioned, the validator rejects it.

## How each hard part is solved

### 1. Grounding
Prices, GST slabs, and stock levels come from the DB via repository queries inside service methods. The `add_to_bill` tool resolves `product_name` → `product_id` → fetches MRP and GST from the row. Numbers are never invented by the model.

### 2. Oversell guard
`billing_service.finalize_bill()` checks `product.stock_quantity < item.quantity` for every line item before decrementing. If any SKU has insufficient stock, the entire bill is rolled back with an explicit error message. This is enforced at the service layer, not the prompt.

### 3. GST correctness
MRP is tax-inclusive, so base price is back-calculated: `base = total / (1 + rate/100)`. GST is split 50/50 into CGST and SGST (intra-state), with odd-rupee rounding carried to SGST. `cgst_amount` and `sgst_amount` are stored per `BillItem` at finalize time (snapshot principle — tax rates can change, but finalized bills stay correct).

### 4. Multi-turn bills
`add_to_bill` creates a draft `Bill` on first call and appends items on subsequent calls. Stock is only decremented at `finalize_bill` time. The draft persists across messages until finalized or abandoned.

### 5. Idempotency
Telegram's `update_id` is used as an idempotency key at the webhook entry point. Before the orchestrator runs, `main.py` checks the `ProcessedUpdate` table. If the `update_id` was already processed, the cached reply is returned immediately. This protects all operations (billing, inventory, khata) from Telegram's at-least-once delivery.

### 6. Concurrency
[Not yet implemented — next on the list]

### 7. Guardrails
- **Sell below cost**: `inventory_service.add_product()` and `receive_stock()` reject any configuration where MRP < cost price.
- **Delete stock**: no delete-stock tool exists. Stock only decreases via `finalize_bill`, which has the oversell guard.
- **Nonexistent khata**: `khata_service.record_payment()` returns an error if the customer account doesn't exist.

### 8. Real artifacts
- **PDF invoices**: [Not yet implemented]
- **PPTX analysis deck**: [Not yet implemented]

### 9. Memory across sessions
`OwnerPreference` table exists in the schema. Tools to set/get preferences and a `/new` chat command that clears conversation while keeping preferences are pending implementation.
