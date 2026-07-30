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
from app.schemas.billing_schemas import (
    AddToBillInput, FinalizeBillInput, QuerySalesInput,
    RemoveFromBillInput, UpdateBillItemInput, ViewDraftBillInput,
    GenerateInvoiceInput, GenerateAnalysisInput,
)

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
    """Single flat agent with all store tools — no routing, no subagents."""

    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self.memory = GLOBAL_MEMORY

        all_tools = [
            # Inventory
            self._wrap(inv.add_product, AddProductInput),
            self._wrap(inv.receive_stock, ReceiveStockInput),
            self._wrap(inv.query_stock, QueryStockInput),
            # Billing
            self._wrap(bill.add_to_bill, AddToBillInput),
            self._wrap(bill.remove_from_bill, RemoveFromBillInput),
            self._wrap(bill.update_bill_item, UpdateBillItemInput),
            self._wrap(bill.view_draft_bill, ViewDraftBillInput),
            self._wrap(bill.finalize_bill, FinalizeBillInput),
            self._wrap(bill.query_todays_sales, QuerySalesInput),
            self._wrap(bill.generate_invoice, GenerateInvoiceInput),
            self._wrap(bill.generate_analysis_deck, GenerateAnalysisInput),
            # Khata
            self._wrap(khata.open_khata_account, OpenKhataInput),
            self._wrap(khata.record_khata_payment, RecordKhataPaymentInput),
            self._wrap(khata.get_khata_balance, GetKhataBalanceInput),
        ]

        import os
        import httpx

        http_client = httpx.Client(
            http2=False,
            headers={"Connection": "close", "Accept-Encoding": "gzip, deflate"},
            timeout=60.0,
        )

        model = ChatOpenAI(
            model="deepseek-v4-flash",
            temperature=0,
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com/v1",
            http_client=http_client,
            callbacks=[AgentCallback()],
        )

        self.agent = create_deep_agent(
            model=model,
            tools=all_tools,
            system_prompt="""You are a professional supermarket assistant. Be concise and direct — no slang, no filler words.

Call tools immediately. Never invent prices, stock, or product names. Never mention internal details like tool names, file paths, or system architecture.
Bills: add items, then finalize. For PDF invoices or PPT analysis decks, use the available tools.
If data is unavailable, state what's available and suggest next steps. Be brief — one or two lines when possible.""",
            checkpointer=self.memory,
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
        """Process a message from the user with memory."""
        import time
        log.info(f"chat_id={self.chat_id} | ▶ handle_message: '{message[:120]}'")
        start = time.time()
        config = {"configurable": {"thread_id": str(self.chat_id)}}
        result = self.agent.invoke(
            {"messages": [HumanMessage(content=message)]},
            config=config,
        )
        elapsed = time.time() - start
        reply = result["messages"][-1].content
        log.info(f"chat_id={self.chat_id} | ◀ handle_message done in {elapsed:.1f}s: '{reply[:200]}'")
        return reply
