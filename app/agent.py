from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from langchain_core.callbacks import BaseCallbackHandler
from sqlmodel import Session
from app.database import engine

import app.tools.inventory as inv
from app.schemas.inventory_schemas import AddProductInput, ReceiveStockInput, QueryStockInput

import app.tools.billing as bill
from app.schemas.billing_schemas import AddToBillInput, FinalizeBillInput, QuerySalesInput, RemoveFromBillInput, UpdateBillItemInput, ViewDraftBillInput, GenerateInvoiceInput

import app.tools.khata as khata
from app.schemas.khata_schemas import OpenKhataInput, RecordKhataPaymentInput, GetKhataBalanceInput

from langgraph.checkpoint.memory import MemorySaver

from app.logger import setup_logger

log = setup_logger("agent")
GLOBAL_MEMORY = MemorySaver()


class AgentCallback(BaseCallbackHandler):
    """Logs LLM calls and tool invocations for observability."""

    def on_llm_start(self, serialized, prompts, **kwargs):
        name = serialized.get("name", serialized.get("id", ["LLM"])[-1]) if serialized else "LLM"
        for p in prompts[:1]:
            log.info(f"[LLM → {name}] prompt={p[:200].replace(chr(10), ' ')}...")

    def on_llm_end(self, response, **kwargs):
        content = ""
        if response.generations and response.generations[0]:
            msg = response.generations[0][0]
            # msg can be AIMessage (has .content) or ChatGeneration (has .text)
            if hasattr(msg, "content"):
                content = str(msg.content or "")
            elif hasattr(msg, "text"):
                content = msg.text or ""
        usage = response.llm_output.get("token_usage", {}) if response.llm_output else {}
        tokens_in = usage.get("prompt_tokens", "?")
        tokens_out = usage.get("completion_tokens", "?")
        log.info(f"[LLM ←] tokens_in={tokens_in} tokens_out={tokens_out} | {content[:200]}")

    def on_tool_start(self, serialized, input_str, **kwargs):
        name = serialized.get("name", "tool")
        log.info(f"[TOOL → {name}] args={str(input_str)[:200]}")

    def on_tool_end(self, output, **kwargs):
        log.info(f"[TOOL ←] result={str(output)[:200]}")

class StoreAgentOrchestrator:
    """
    A Multi-Agent Orchestrator using deepagents create_deep_agent.
    """
    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self.memory = GLOBAL_MEMORY
        
        # Tools
        inventory_tools = [
            self._wrap(inv.add_product, AddProductInput),
            self._wrap(inv.receive_stock, ReceiveStockInput),
            self._wrap(inv.query_stock, QueryStockInput),
        ]
        
        billing_tools = [
            self._wrap(bill.add_to_bill, AddToBillInput),
            self._wrap(bill.remove_from_bill, RemoveFromBillInput),
            self._wrap(bill.update_bill_item, UpdateBillItemInput),
            self._wrap(bill.view_draft_bill, ViewDraftBillInput),
            self._wrap(bill.finalize_bill, FinalizeBillInput),
            self._wrap(bill.query_todays_sales, QuerySalesInput),
            self._wrap(bill.generate_invoice, GenerateInvoiceInput),
        ]
        
        khata_tools = [
            self._wrap(khata.open_khata_account, OpenKhataInput),
            self._wrap(khata.record_khata_payment, RecordKhataPaymentInput),
            self._wrap(khata.get_khata_balance, GetKhataBalanceInput),
        ]

        import os
        import httpx

        http_client = httpx.Client(
            http2=False,
            headers={"Connection": "close", "Accept-Encoding": "gzip, deflate"},
            timeout=60.0
        )

        def _make_model():
            return ChatOpenAI(
                model="deepseek-v4-flash",
                temperature=0,
                api_key=os.getenv("DEEPSEEK_API_KEY"),
                base_url="https://api.deepseek.com/v1",
                http_client=http_client,
                callbacks=[AgentCallback()],
            )

        # Orchestrator routes only
        routing_model = _make_model()
        # Subagents share the same model config
        reasoning_model = _make_model()

        # SubAgents — model has thinking=False so tool_choice='required' is accepted
        # tool_choice is passed via model_kwargs so deepagents sees a plain BaseChatModel
        inventory_agent = {
            "name": "inventory_agent",
            "description": "Manage stock, add products, or check inventory levels.",
            "system_prompt": "You are the Inventory Manager. Call the appropriate tool immediately. To add a product: call add_product. To check stock: call query_stock. To receive stock: call receive_stock. If required fields are missing, ask the user once.",
            "tools": inventory_tools,
            "model": reasoning_model,
        }

        billing_agent = {
            "name": "billing_agent",
            "description": "Create bills, add items to a bill, finalize sales, generate PDF invoices, show daily sales.",
            "system_prompt": "You are the Billing Agent. Call tools immediately — never type a bill or invoice yourself. To create a bill: call add_to_bill for each item. To show the bill: call view_draft_bill. To finalize: call finalize_bill. For sales: call query_todays_sales. For PDF: call generate_invoice. If the tool returns an error, report it.",
            "tools": billing_tools,
            "model": reasoning_model,
        }

        khata_agent = {
            "name": "khata_agent",
            "description": "Manage customer credit, check balances, or record payments.",
            "system_prompt": "You are the Khata Manager. Call tools immediately. Only ask if a required field (customer name or amount) is missing.",
            "tools": khata_tools,
            "model": reasoning_model,
        }

        # Orchestrator routes to subagents — it MUST delegate, never answer
        self.orchestrator = create_deep_agent(
            model=routing_model,
            system_prompt="""CRITICAL: You are ONLY a router. You CANNOT answer queries — you MUST delegate to a subagent.

inventory_agent → stock, products, shipments
billing_agent → bills, sales, invoices, PDF
khata_agent → credit, balances, payments

Delegate now. If off-topic only: 'I can only assist with inventory, billing, and credit ledgers.'""",
            subagents=[inventory_agent, billing_agent, khata_agent],
            checkpointer=self.memory,
            debug=True,
        )

    def _wrap(self, func, schema):
        def wrapper(**kwargs):
            from sqlmodel import Session
            with Session(engine) as session:
                return func(session, self.chat_id, schema(**kwargs))
        return StructuredTool.from_function(
            func=wrapper,
            name=func.__name__,
            description=func.__doc__,
            args_schema=schema,
        )

    def handle_message(self, message: str) -> str:
        """Process a message from the user via the Orchestrator with memory."""
        import time
        log.info(f"chat_id={self.chat_id} | ▶ handle_message: '{message[:120]}'")
        start = time.time()
        config = {"configurable": {"thread_id": str(self.chat_id)}}
        result = self.orchestrator.invoke(
            {"messages": [HumanMessage(content=message)]},
            config=config
        )
        elapsed = time.time() - start
        reply = result["messages"][-1].content
        log.info(f"chat_id={self.chat_id} | ◀ handle_message done in {elapsed:.1f}s: '{reply[:200]}'")
        return reply
