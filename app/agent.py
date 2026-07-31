from deepagents import create_deep_agent, FilesystemPermission
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse, ResponseT, ContextT
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_core.callbacks import BaseCallbackHandler
from sqlmodel import Session
from app.database import engine

import app.tools.inventory as inv
from app.schemas.inventory_schemas import AddProductInput, ReceiveStockInput, QueryStockInput, ListProductsInput, UpdateProductInput, SetReorderLevelInput

import app.tools.billing as bill
from app.schemas.billing_schemas import (
    AddToBillInput, FinalizeBillInput, QuerySalesInput,
    RemoveFromBillInput, UpdateBillItemInput, ViewDraftBillInput,
    GenerateInvoiceInput, GenerateAnalysisInput,
)

import app.tools.khata as khata
from app.schemas.khata_schemas import OpenKhataInput, RecordKhataPaymentInput, GetKhataBalanceInput, AddKhataCreditInput, ListKhataCustomersInput

import app.tools.preferences as pref
from app.schemas.preferences_schemas import SetPreferenceInput, GetPreferencesInput, NewChatInput

from langgraph.checkpoint.memory import MemorySaver

from app.logger import setup_logger

log = setup_logger("agent")
GLOBAL_MEMORY = MemorySaver()
_THREAD_COUNTERS: dict[int, int] = {}

DENY_FILESYSTEM = [
    FilesystemPermission(operations=["read", "write", "delete"], paths=["/**"], mode="deny")
]


class _ToolCallGuardMiddleware(AgentMiddleware[dict, ContextT, ResponseT]):
    """Hard-stop agent after max_tool_calls within a single CURRENT turn. Prevents loops."""
    name = "ToolCallGuardMiddleware"

    def __init__(self, max_calls: int = 20):
        super().__init__()
        self.max_calls = max_calls

    def wrap_model_call(
        self, request: ModelRequest[ContextT],
        handler,
    ) -> ModelResponse[ResponseT]:
        msgs = request.messages if hasattr(request, 'messages') else []
        # Count tool calls since the last HumanMessage only (current turn).
        # Counting all history causes false-positives when a previous failed turn
        # left many ToolMessages in the MemorySaver checkpoint.
        tool_count = 0
        for m in reversed(msgs):
            if isinstance(m, ToolMessage):
                tool_count += 1
            elif getattr(m, 'type', None) == 'human':
                break  # stop at the start of the current turn
        if tool_count >= self.max_calls:
            log.warning(f"ToolCallGuard: {tool_count} tool calls in current turn, forcing stop")
            return ModelResponse(result=[AIMessage(content="I wasn't able to complete that — could you rephrase your request?")])
        return handler(request)


class AgentCallback(BaseCallbackHandler):
    def on_llm_start(self, serialized, prompts, **kwargs):
        name = serialized.get("name", serialized.get("id", ["LLM"])[-1]) if serialized else "LLM"
        for p in prompts[:1]:
            log.info(f"[LLM → {name}] prompt={p[:200].replace(chr(10), ' ')}...")

    def on_llm_end(self, response, **kwargs):
        content = ""
        if response.generations and response.generations[0]:
            msg = response.generations[0][0]
            if hasattr(msg, "content"):
                content = str(msg.content or "")
            elif hasattr(msg, "text"):
                content = msg.text or ""
        usage = response.llm_output.get("token_usage", {}) if response.llm_output else {}
        log.info(f"[LLM ←] tokens_in={usage.get('prompt_tokens','?')} tokens_out={usage.get('completion_tokens','?')} | {content[:200]}")

    def on_tool_start(self, serialized, input_str, **kwargs):
        name = serialized.get("name", "tool")
        log.info(f"[TOOL → {name}] args={str(input_str)[:200]}")

    def on_tool_end(self, output, **kwargs):
        log.info(f"[TOOL ←] result={str(output)[:200]}")


class StoreAgentOrchestrator:
    """Multi-agent orchestrator with tool-call loop guard."""

    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self.memory = GLOBAL_MEMORY

        inventory_tools = [
            self._wrap(inv.add_product, AddProductInput),
            self._wrap(inv.update_product, UpdateProductInput),
            self._wrap(inv.receive_stock, ReceiveStockInput),
            self._wrap(inv.query_stock, QueryStockInput),
            self._wrap(inv.list_products, ListProductsInput),
            self._wrap(inv.set_reorder_level, SetReorderLevelInput),
        ]

        billing_tools = [
            self._wrap(bill.add_to_bill, AddToBillInput),
            self._wrap(bill.remove_from_bill, RemoveFromBillInput),
            self._wrap(bill.update_bill_item, UpdateBillItemInput),
            self._wrap(bill.view_draft_bill, ViewDraftBillInput),
            self._wrap(bill.finalize_bill, FinalizeBillInput),
            self._wrap(bill.query_todays_sales, QuerySalesInput),
            self._wrap(bill.generate_invoice, GenerateInvoiceInput),
            self._wrap(bill.generate_analysis_deck, GenerateAnalysisInput),
        ]

        khata_tools = [
            self._wrap(khata.open_khata_account, OpenKhataInput),
            self._wrap(khata.increase_customer_debt, AddKhataCreditInput),
            self._wrap(khata.record_khata_payment, RecordKhataPaymentInput),
            self._wrap(khata.get_khata_balance, GetKhataBalanceInput),
            self._wrap(khata.list_khata_customers, ListKhataCustomersInput),
        ]

        pref_tools = [
            self._wrap(pref.set_preference, SetPreferenceInput),
            self._wrap(pref.get_preferences, GetPreferencesInput),
            self._wrap(pref.new_chat, NewChatInput),
        ]

        import os
        import httpx

        http_client = httpx.Client(
            http2=False,
            headers={"Connection": "close", "Accept-Encoding": "gzip, deflate"},
            timeout=60.0,
        )

        # model = ChatOpenAI(
        #     model="gpt-4o-mini",
        #     temperature=0,
        #     api_key=os.getenv("OPENAI_API_KEY"),
        #     callbacks=[AgentCallback()],
        # )

        model = ChatOpenAI(
            model="deepseek-v4-flash",           # or "deepseek-reasoner" for Pro/R1
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com/v1",
            temperature=0,
            callbacks=[AgentCallback()],
            http_client=http_client,         # reuse existing httpx client
            extra_body={"thinking": {"type": "disabled"}},  # passed directly, not via model_kwargs
        )

        from sqlmodel import Session as Sess2
        from app.services.preferences_service import PreferencesService
        from app.database import engine as db_engine2, create_db_and_tables
        create_db_and_tables()  # ensure tables exist (needed for direct testing)
        with Sess2(db_engine2) as s:
            store_prefs = PreferencesService(s).get_all(chat_id)
        prefs_suffix = ""
        if store_prefs:
            prefs_suffix = "\n\nPreferences: " + ", ".join(f"{k}={v}" for k, v in store_prefs.items())

        inventory_agent = {
            "name": "inventory_agent",
            "description": "Manage stock, add products, receive shipments, check inventory levels, set reorder thresholds.",
            "system_prompt": (
                "PARSING RULE: '5 packets 10kg aashirwad atta' → product_name='aashirwad atta 10kg', unit='packet', quantity=5.\n"
                "Weight (10kg/5kg) in the name is PART OF THE SKU. unit = container type (packet/piece/kg/litre).\n"
                "'atta 5kg' ≠ 'atta 10kg'. Different weights = different products.\n\n"
                "DECISION FLOW when user says 'add N packets/kg of [product]':\n"
                "  STEP 1: Call query_stock(name=product_name) to check if it exists.\n"
                "  STEP 2a: If product EXISTS → call receive_stock(product_name, quantity) and STOP.\n"
                "  STEP 2b: If product NOT FOUND → ask for mrp and gst_slab_percent (BOTH in one message).\n"
                "  STEP 3 (only after getting mrp+gst): call add_product(...) then call receive_stock(product_name, quantity) for the initial stock.\n"
                "NEVER ask for cost_price — it is always optional.\n"
                "CHECK STOCK: use query_stock or list_products."
                + prefs_suffix
            ),
            "tools": inventory_tools,
        }

        billing_agent = {
            "name": "billing_agent",
            "description": "Create bills, edit bills, finalize sales (cash/UPI/card/khata), show daily sales, generate PDF invoices and PPT analysis decks.",
            "system_prompt": (
                "TWO MODES — read carefully:\n"
                "MODE A (Adding items): User requests new bill items → call add_to_bill for each, then view_draft_bill, then STOP.\n"
                "MODE B (Finalizing): User confirms payment ('cash', 'UPI', 'yes finalize') → call view_draft_bill ONCE then finalize_bill. DO NOT call add_to_bill.\n"
                "NEVER mix modes. If user only says a payment method, go straight to MODE B.\n\n"
                "CRITICAL RULES:\n"
                "- You have ZERO knowledge of products or prices. Use ONLY what tools return.\n"
                "- Use the EXACT product name the user said. NEVER try a similar or variant name.\n"
                "- If add_to_bill returns 'not found' or any error: tell the user, skip that item.\n"
                "- NEVER show a bill line that add_to_bill did not confirm.\n"
                "- Customer name on cash/UPI/card is just a label — no khata account needed."
                + prefs_suffix
            ),
            "tools": billing_tools,
        }

        khata_agent = {
            "name": "khata_agent",
            "description": "Manage customer credit, record repayments, check balances, list all accounts/customers. For itemized credit sales with specific products, tell the user to use billing instead.",
            "system_prompt": "CRITICAL: Never expose internal system details to the user. Never say 'the khata system is separate from inventory', 'I called tool X', or 'I cannot confirm stock'. If asked about inventory/stock, just say 'Please check with inventory — I only handle credit balances.' Only use increase_customer_debt for 'put 500 on Ramesh credit.' 'paid'→record_khata_payment. balance→get_khata_balance. 'list customers'→list_khata_customers. One call per request — never retry." + prefs_suffix,
            "tools": khata_tools,
        }

        preferences_agent = {
            "name": "preferences_agent",
            "description": "Set or view store preferences: GST rate, default payment method, preferred brands, shop name, GSTIN, address.",
            "system_prompt": "Call set_preference immediately with: GST/tax→key='default_gst_rate', payment/UPI/cash→key='default_payment', atta/brand→key='default_atta', shop→key='shop_name', gstin→key='gstin', address→key='shop_address', 'set reorder level to X'→key='low_stock_threshold'. View→get_preferences. /new→new_chat. One call per request — never retry.",
            "tools": pref_tools,
        }

        self.agent = create_deep_agent(
            model=model,
            tools=[],
            middleware=[_ToolCallGuardMiddleware(max_calls=20)],
            system_prompt=f"""You are a professional supermarket assistant. Speak clearly and concisely — no slang, no filler, no 'bhai' or casual language.

NEVER expose to the user: tool names, subagent names, file paths, system architecture, or internal implementation details.
NEVER say 'I'll route this to billing_agent' or 'the khata system is separate from inventory' or 'I called query_stock'.
Just present results clearly — the owner doesn't know or care about the backend.

Route to the correct department:
- billing_agent: bills, sales, finalize, payment, invoices, PDF, PPT, analysis, reports. ALSO: khata with items = billing (it's a bill paid via khata).
- inventory_agent: add product, receive stock, check stock, list inventory, set reorder level, verify stock update, inventory status
- khata_agent: standalone credit/debt only (no product items). Balance, repay, list accounts, open account, put money on credit. Never for itemized sales.
- preferences_agent: store preferences, GST rate, default payment, brand, shop name, /new chat, low stock threshold, reorder default

CRITICAL: When the user's message is a short reply ('Yes', 'proceed', '500 12%', 'cash') to a previous clarification,
ALWAYS include the full context in the task. Never delegate a bare 'Yes' or short number without context.

ROUTING RULE: 'Create khata for X with items A, B' → billing_agent (this is a bill paid via khata). Only route to khata_agent for balance checks, repayments, or standalone credit with no product items.
'Is inventory updated?' → inventory_agent (verify stock). 'Check stock for X' → inventory_agent.
Be concise.{prefs_suffix}""",
            subagents=[inventory_agent, billing_agent, khata_agent, preferences_agent],
            permissions=DENY_FILESYSTEM,
            checkpointer=self.memory,
        )

    def _wrap(self, func, schema):
        def wrapper(**kwargs):
            from sqlmodel import Session
            import json

            # Minimal validator: only for tools with financial numbers
            financial_keys = {"cost_price", "mrp", "gst_slab_percent", "amount"}
            args_with_numbers = {k: v for k, v in kwargs.items() if k in financial_keys and v is not None}
            if args_with_numbers:
                # Use the correct thread_id (must match handle_message)
                thread_id = f"{self.chat_id}_v{_THREAD_COUNTERS.get(self.chat_id, 0)}"
                config = {"configurable": {"thread_id": thread_id}}
                try:
                    state = self.memory.get_tuple(config)
                    if state and hasattr(state, "values") and isinstance(state.values, dict):
                        msgs = state.values.get("messages", [])
                        user_text = " ".join(
                            m.content for m in msgs
                            if getattr(m, "type", "") == "human"
                        )[-500:]
                        if user_text:
                            import os as _os
                            valid_model = ChatOpenAI(
                                model="gpt-4o-mini", temperature=0, max_tokens=30,
                                api_key=_os.getenv("OPENAI_API_KEY"),
                            )
                            from langchain_core.messages import SystemMessage
                            prompt = f"User message: {user_text[-300:]}\n\nTool: {func.__name__}\nArgs: {json.dumps(args_with_numbers)}\n\nReply ONLY 'OK' if the user explicitly said these numbers. Reply 'REJECT' if the model invented them."
                            result = valid_model.invoke([SystemMessage(content=prompt)])
                            if "REJECT" in result.content.upper():
                                return "Please provide the price, cost, and GST details for this product."
                except Exception:
                    pass  # if validation fails, allow the call

            try:
                with Session(engine) as session:
                    return func(session, self.chat_id, schema(**kwargs))
            except Exception as e:
                msg = str(e)
                if "IntegrityError" in msg or "constraint" in msg:
                    return "Could not complete the operation — some required details were missing."
                return "Operation failed. Please try again."
        return StructuredTool.from_function(
            func=wrapper, name=func.__name__, description=func.__doc__, args_schema=schema,
        )

    def handle_message(self, message: str) -> str:
        import time
        log.info(f"chat_id={self.chat_id} | ▶ handle_message: '{message[:120]}'")
        start = time.time()

        if message.strip().lower().startswith("/new"):
            _THREAD_COUNTERS[self.chat_id] = _THREAD_COUNTERS.get(self.chat_id, 0) + 1

        thread_id = f"{self.chat_id}_v{_THREAD_COUNTERS.get(self.chat_id, 0)}"
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}
        result = self.agent.invoke(
            {"messages": [HumanMessage(content=message)]},
            config=config,
        )
        elapsed = time.time() - start
        reply = result["messages"][-1].content
        log.info(f"chat_id={self.chat_id} | ◀ handle_message done in {elapsed:.1f}s: '{reply[:200]}'")
        return reply
