from deepagents import create_deep_agent, FilesystemPermission
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse, ResponseT, ContextT
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
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
_THREAD_COUNTERS: dict[int, int] = {}

DENY_FILESYSTEM = [
    FilesystemPermission(operations=["read", "write", "delete"], paths=["/**"], mode="deny")
]


class _ToolCallGuardMiddleware(AgentMiddleware[dict, ContextT, ResponseT]):
    """Hard-stop agent after max_tool_calls within a single turn. Prevents loops."""
    name = "ToolCallGuardMiddleware"

    def __init__(self, max_calls: int = 20):
        super().__init__()
        self.max_calls = max_calls

    def wrap_model_call(
        self, request: ModelRequest[ContextT],
        handler,
    ) -> ModelResponse[ResponseT]:
        msgs = request.messages if hasattr(request, 'messages') else []
        tool_count = sum(1 for m in msgs if isinstance(m, ToolMessage))
        if tool_count >= self.max_calls:
            log.warning(f"ToolCallGuard: {tool_count} tool calls, forcing stop")
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
            "description": "Manage stock, add products, receive shipments, check inventory levels.",
            "system_prompt": "Add products, receive shipments, check stock, list inventory. One call per request — never retry. Apply preferences when present." + prefs_suffix,
            "tools": inventory_tools,
        }

        billing_agent = {
            "name": "billing_agent",
            "description": "Create bills, edit bills, finalize sales (cash/UPI/card/khata), show daily sales, generate PDF invoices and PPT analysis decks. Handle 'sell X to customer' as a normal bill — the customer name is just a reference unless paying via khata.",
            "system_prompt": "FLOW: 1) add_to_bill each item ONE AT A TIME 2) view_draft 3) finalize_bill with payment mode and customer name if provided. DO NOT parallelize add_to_bill. Customer name on cash/UPI/card is just a label — no khata account needed. Apply preferences (default_payment). One call per request — never retry." + prefs_suffix,
            "tools": billing_tools,
        }

        khata_agent = {
            "name": "khata_agent",
            "description": "Manage customer credit, record repayments, check balances, list all accounts/customers.",
            "system_prompt": "'put on credit'→increase_customer_debt. 'paid'→record_khata_payment. balance→get_khata_balance. 'list customers'/'show accounts'/'my accounts'→list_khata_customers. One call per request — never retry." + prefs_suffix,
            "tools": khata_tools,
        }

        preferences_agent = {
            "name": "preferences_agent",
            "description": "Set or view store preferences: GST rate, default payment method, preferred brands, shop name, GSTIN, address.",
            "system_prompt": "Call set_preference immediately with: GST/tax→key='default_gst_rate', payment/UPI/cash→key='default_payment', atta/brand→key='default_atta', shop→key='shop_name', gstin→key='gstin', address→key='shop_address'. View→get_preferences. /new→new_chat. One call per request — never retry.",
            "tools": pref_tools,
        }

        self.agent = create_deep_agent(
            model=model,
            tools=[],
            middleware=[_ToolCallGuardMiddleware(max_calls=20)],
            system_prompt=f"""Route requests to the right department:
- billing_agent: bills, sales, invoices, PDF, PPT, analysis, reports
- inventory_agent: stock, products, shipments, stock levels
- khata_agent: customer khata/credit, increase debt, repay, balance, list accounts/customers
- preferences_agent: store preferences, GST rate, default payment, brand, shop name, /new chat

Delegate immediately. Be concise. Never mention tool names or internals.{prefs_suffix}""",
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
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 25}
        result = self.agent.invoke(
            {"messages": [HumanMessage(content=message)]},
            config=config,
        )
        elapsed = time.time() - start
        reply = result["messages"][-1].content
        log.info(f"chat_id={self.chat_id} | ◀ handle_message done in {elapsed:.1f}s: '{reply[:200]}'")
        return reply
