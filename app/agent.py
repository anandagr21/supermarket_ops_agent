from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from sqlmodel import Session
from app.database import engine

import app.tools.inventory as inv
from app.schemas.inventory_schemas import AddProductInput, ReceiveStockInput, QueryStockInput

import app.tools.billing as bill
from app.schemas.billing_schemas import AddToBillInput, FinalizeBillInput, QuerySalesInput, RemoveFromBillInput, UpdateBillItemInput, ViewDraftBillInput, GenerateInvoiceInput

import app.tools.khata as khata
from app.schemas.khata_schemas import OpenKhataInput, RecordKhataPaymentInput, GetKhataBalanceInput

from langgraph.checkpoint.memory import MemorySaver
GLOBAL_MEMORY = MemorySaver()

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

        def _make_model(max_tokens: int | None = None):
            return ChatOpenAI(
                model="deepseek-v4-flash",
                temperature=0,
                max_tokens=max_tokens,
                api_key=os.getenv("DEEPSEEK_API_KEY"),
                base_url="https://api.deepseek.com/v1",
                http_client=http_client,
            )

        # Orchestrator gets low max_tokens — it just routes, no long replies
        routing_model = _make_model(max_tokens=256)
        # Subagents get full reasoning capacity
        reasoning_model = _make_model()
        # Validator only needs JSON, not prose
        validator_model = _make_model(max_tokens=64)

        # SubAgents
        inventory_agent = {
            "name": "inventory_agent",
            "description": "Manage stock, add products, or check inventory levels.",
            "system_prompt": "You are the Inventory Manager. Execute tool calls immediately. NEVER confirm or recap — just act. Only ask questions if a required field (unit, gst, cost, mrp) was not provided at all.",
            "tools": inventory_tools,
            "model": reasoning_model,
        }

        billing_agent = {
            "name": "billing_agent",
            "description": "Create bills, add items, finalize sales, generate PDF invoices, show daily sales.",
            "system_prompt": "You are the Billing Cashier. Execute tool calls immediately. NEVER confirm or recap — just act. Only ask if a required field (product, quantity, payment mode) was not provided at all. For 'send me the invoice/bill as PDF', call generate_invoice.",
            "tools": billing_tools,
            "model": reasoning_model,
        }

        khata_agent = {
            "name": "khata_agent",
            "description": "Manage customer credit, check balances, or record payments.",
            "system_prompt": "You are the Khata Manager. Execute tool calls immediately. NEVER confirm or recap — just act. Only ask if a required field was not provided at all.",
            "tools": khata_tools,
            "model": reasoning_model,
        }

        self._validator_model = validator_model  # stash for _wrap

        # Orchestrator uses low-token model — routing is classification, not generation
        self.orchestrator = create_deep_agent(
            model=routing_model,
            system_prompt="""Route to the correct agent. Do NOT answer yourself.
- inventory_agent: stock, products, shipments
- billing_agent: bills, sales, invoices, PDF generation, daily reports
- khata_agent: credit, balances, payments

Route and stop. If off-topic: 'I can only assist with inventory, billing, and credit ledgers.'""",
            subagents=[inventory_agent, billing_agent, khata_agent],
            checkpointer=self.memory
        )

    def _wrap(self, func, schema):
        def wrapper(**kwargs):
            import json
            import os
            import httpx
            from langchain_core.messages import SystemMessage
            from langchain_openai import ChatOpenAI
            from sqlmodel import Session
            
            # 1. Fetch recent chat history
            config = {"configurable": {"thread_id": str(self.chat_id)}}
            chat_history = ""
            try:
                state_tuple = self.memory.get_tuple(config)
                if state_tuple and hasattr(state_tuple, "values") and isinstance(state_tuple.values, dict):
                    msgs = state_tuple.values.get("messages", [])
                    user_msgs = [m.content for m in msgs if getattr(m, "type", "") == "human"]
                    chat_history = "\n".join(user_msgs[-3:])
            except Exception:
                pass
            
            # 2. Invoke Validator LLM
            validator_prompt = f"""You are the Tool Execution Guardrail.
The agent wants to execute the tool '{func.__name__}' with these arguments:
{json.dumps(kwargs)}

Here is the user's recent chat history:
---
{chat_history}
---

Did the user EXPLICITLY provide all the numerical values (like price, gst) present in the tool arguments? 
If the agent hallucinated ANY numbers (e.g., guessing a price or GST), you must reject it.
Respond strictly with a JSON object: {{"approved": true/false, "reason": "..."}}
"""
            
            validator_llm = self._validator_model
            
            try:
                validation_result = validator_llm.invoke([SystemMessage(content=validator_prompt)])
                raw_json = validation_result.content.replace("```json", "").replace("```", "").strip()
                result_data = json.loads(raw_json)
                if not result_data.get("approved", True):
                    return f"Error: Tool execution rejected by Validator Agent. Reason: {result_data.get('reason')}. Please ask the user for the missing details."
            except Exception as e:
                pass # Fallback to allow if validation fails
                
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
        config = {"configurable": {"thread_id": str(self.chat_id)}}
        result = self.orchestrator.invoke(
            {"messages": [HumanMessage(content=message)]},
            config=config
        )
        return result["messages"][-1].content
