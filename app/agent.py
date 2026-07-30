from deepagents import create_deep_agent, FilesystemPermission
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from langchain_core.callbacks import BaseCallbackHandler
from sqlmodel import Session
from app.database import engine

import app.tools.inventory as inv
from app.schemas.inventory_schemas import AddProductInput, ReceiveStockInput, QueryStockInput, ListProductsInput

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
_THREAD_COUNTERS: dict[int, int] = {}  # chat_id → counter for /new thread switching

# Deny ALL filesystem operations globally — our tools talk to a DB, not disk
DENY_FILESYSTEM = [
    FilesystemPermission(
        operations=["read", "write", "delete"],
        paths=["/**"],
        mode="deny",
    ),
]


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
    """Multi-agent orchestrator with filesystem denied via permissions."""

    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self.memory = GLOBAL_MEMORY

        inventory_tools = [
            self._wrap(inv.add_product, AddProductInput),
            self._wrap(inv.receive_stock, ReceiveStockInput),
            self._wrap(inv.query_stock, QueryStockInput),
            self._wrap(inv.list_products, ListProductsInput),
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

        model = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            api_key=os.getenv("OPENAI_API_KEY"),
            callbacks=[AgentCallback()],
        )

        # Fetch preferences BEFORE building subagents so prompts get injected
        from sqlmodel import Session as Sess2
        from app.services.preferences_service import PreferencesService
        from app.database import engine as db_engine2
        with Sess2(db_engine2) as s:
            store_prefs = PreferencesService(s).get_all(chat_id)
        prefs_suffix = ""
        if store_prefs:
            prefs_suffix = "\n\nPreferences: " + ", ".join(
                f"{k}={v}" for k, v in store_prefs.items()
            )

        inventory_agent = {
            "name": "inventory_agent",
            "description": "Manage stock, add products, receive shipments, check inventory levels.",
            "system_prompt": "You are the Inventory Manager. Add products, receive shipments, check stock. Apply standing preferences when adding (e.g. default GST). Never mention tool names." + prefs_suffix,
            "tools": inventory_tools,
        }

        billing_agent = {
            "name": "billing_agent",
            "description": "Create bills, edit bills, finalize sales, show daily sales, generate PDF invoices, generate PPT analysis decks.",
            "system_prompt": "You are the Billing Cashier. FLOW: 1) add_to_bill ALL items parallel, 2) view_draft_bill, 3) finalize_bill. Edits: update_bill_item SETS qty, remove_from_bill drops. Apply standing preferences (e.g. default_payment). Never create a new bill when draft exists." + prefs_suffix,
            "tools": billing_tools,
        }

        khata_agent = {
            "name": "khata_agent",
            "description": "Increase customer debt on credit, record repayments, check balances, list all customers.",
            "system_prompt": "You are the Khata Manager. 'put on credit' → increase_customer_debt. 'paid' → record_khata_payment. balance → get_khata_balance. 'list customers' → list_khata_customers. Never mention tool names." + prefs_suffix,
            "tools": khata_tools,
        }

        preferences_agent = {
            "name": "preferences_agent",
            "description": "Set or view store preferences like default payment method, preferred brands, store name, GST rate. Use set_preference with exact keys.",
            "system_prompt": "You are the Preferences Manager. For setting preferences use these EXACT keys: 'default_payment' (cash/upi/card/khata), 'default_gst_rate' (5/12/18 as number), 'default_atta' (brand), 'shop_name', 'gstin', 'shop_address'. Call set_preference(key, value) directly — don't ask for confirmation. Call get_preferences to view. /new → new_chat.",
            "tools": pref_tools,
        }

        self.agent = create_deep_agent(
            model=model,
            tools=[],
            system_prompt=f"""Route to the right department:
- billing_agent: bills, sales, invoices, PDF, PPT, analysis, reports
- inventory_agent: stock, products, shipments, stock levels
- khata_agent: customer khata/credit, increase debt, repay, balance
- preferences_agent: store preferences, default payment, brand, shop, /new chat

Delegate. Be concise. Never mention tool names or internals.{prefs_suffix}""",
            subagents=[inventory_agent, billing_agent, khata_agent, preferences_agent],
            permissions=DENY_FILESYSTEM,
            checkpointer=self.memory,
        )

    def _wrap(self, func, schema):
        def wrapper(**kwargs):
            from sqlmodel import Session
            try:
                with Session(engine) as session:
                    return func(session, self.chat_id, schema(**kwargs))
            except Exception as e:
                msg = str(e)
                if "IntegrityError" in msg or "constraint" in msg:
                    return "Could not complete the operation — some required details were missing. Please provide all product details (name, unit, GST%, cost, MRP) and try again."
                return f"Operation failed. Please try again with complete details."
        return StructuredTool.from_function(
            func=wrapper,
            name=func.__name__,
            description=func.__doc__,
            args_schema=schema,
        )

    def handle_message(self, message: str) -> str:
        import time
        log.info(f"chat_id={self.chat_id} | ▶ handle_message: '{message[:120]}'")
        start = time.time()

        # /new chat: bump thread so conversation history resets, data stays in DB
        if message.strip().lower().startswith("/new"):
            _THREAD_COUNTERS[self.chat_id] = _THREAD_COUNTERS.get(self.chat_id, 0) + 1

        thread_id = f"{self.chat_id}_v{_THREAD_COUNTERS.get(self.chat_id, 0)}"
        config = {"configurable": {"thread_id": thread_id}}
        result = self.agent.invoke(
            {"messages": [HumanMessage(content=message)]},
            config=config,
        )
        elapsed = time.time() - start
        reply = result["messages"][-1].content
        log.info(f"chat_id={self.chat_id} | ◀ handle_message done in {elapsed:.1f}s: '{reply[:200]}'")
        return reply
