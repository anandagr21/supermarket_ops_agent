from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from sqlmodel import Session
from app.database import engine

import app.tools.inventory as inv
from app.schemas.inventory_schemas import AddProductInput, ReceiveStockInput, QueryStockInput

import app.tools.billing as bill
from app.schemas.billing_schemas import AddToBillInput, FinalizeBillInput, QuerySalesInput

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
            self._wrap(bill.finalize_bill, FinalizeBillInput),
            self._wrap(bill.query_todays_sales, QuerySalesInput),
        ]
        
        khata_tools = [
            self._wrap(khata.open_khata_account, OpenKhataInput),
            self._wrap(khata.record_khata_payment, RecordKhataPaymentInput),
            self._wrap(khata.get_khata_balance, GetKhataBalanceInput),
        ]

        import os
        import httpx
        
        # Create a custom httpx client to avoid DeepSeek hanging issues
        # (disables brotli and keep-alive)
        http_client = httpx.Client(
            http2=False,
            headers={"Connection": "close", "Accept-Encoding": "gzip, deflate"},
            timeout=60.0
        )

        # Model (using deepseek)
        model = ChatOpenAI(
            model="deepseek-v4-flash",
            temperature=0,
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com/v1",
            http_client=http_client
        )

        # SubAgents
        inventory_agent = {
            "name": "inventory_agent",
            "description": "Manage stock, add products, or check inventory levels.",
            "system_prompt": "You are the Inventory Manager. Help the owner check stock, add products, and receive shipments. Prices are in INR. Be brief. If the user asks to add a product but misses required details (unit, gst, cost price, mrp), DO NOT guess them. Ask the user for the missing fields.",
            "tools": inventory_tools,
            "model": model,
        }

        billing_agent = {
            "name": "billing_agent",
            "description": "Create bills, add items to a bill, or finalize a sale.",
            "system_prompt": "You are the Billing Cashier. Help the owner draft and finalize bills. Prices are in INR. Be brief. If the user misses details (product name, quantity, payment mode), DO NOT guess them. Ask the user.",
            "tools": billing_tools,
            "model": model,
        }

        khata_agent = {
            "name": "khata_agent",
            "description": "Manage customer credit, check balances, or record payments.",
            "system_prompt": "You are the Khata (Credit Ledger) Manager. Help the owner track who owes money and record payments. Prices are in INR. Be brief. If the user misses details (customer name, amount, etc.), DO NOT guess them. Ask the user.",
            "tools": khata_tools,
            "model": model,
        }



        # Orchestrator
        self.orchestrator = create_deep_agent(
            model=model,
            system_prompt="""You are the Store Orchestrator. 
            Your ONLY job is to analyze the user's request and delegate it to the correct specialized department agent.
            If a request is ambiguous, ask the user for clarification before delegating.
            Do not try to answer questions yourself. Delegate everything!
            If the user asks a question that is unrelated to managing the supermarket (inventory, billing, or khata), you MUST refuse to answer and reply exactly with: 'I am a supermarket management agent. I can only assist with inventory, billing, and credit ledgers.'""",
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
            
            validator_llm = ChatOpenAI(
                model="deepseek-v4-flash",
                temperature=0,
                api_key=os.getenv("DEEPSEEK_API_KEY"),
                base_url="https://api.deepseek.com/v1",
                http_client=httpx.Client(http2=False, headers={"Connection": "close", "Accept-Encoding": "gzip, deflate"}, timeout=30.0)
            )
            
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
