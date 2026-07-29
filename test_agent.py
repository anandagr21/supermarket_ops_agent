import os
from dotenv import load_dotenv
from app.database import create_db_and_tables
from app.agent import StoreAgentOrchestrator

load_dotenv()

def run_tests():
    print("Ensuring database is set up...")
    create_db_and_tables()
    
    # We will simulate a user interacting with the bot via Telegram.
    chat_id = 12345
    print(f"\n--- Instantiating Orchestrator for Chat ID: {chat_id} ---")
    orchestrator = StoreAgentOrchestrator(chat_id)
    
    test_queries = [
        "Add a new product called 'Tata Salt', unit is kg, GST is 5%, cost price is 20, MRP is 25.",
        "We just received 100 kg of Tata Salt.",
        "A customer wants to buy 2 kg of Tata Salt.",
        "Finalize the bill. They are paying via Khata. The customer name is 'Ramesh'.",
        "How much does Ramesh owe me now?"
    ]
    
    import traceback
    for query in test_queries:
        print(f"\n[USER]: {query}")
        try:
            response = orchestrator.handle_message(query)
            print(f"[AGENT]: {response}")
        except Exception as e:
            print(f"[ERROR]: {e}")
            traceback.print_exc()

if __name__ == "__main__":
    if not os.getenv("DEEPSEEK_API_KEY"):
        print("Please set DEEPSEEK_API_KEY in your .env file or environment.")
        exit(1)
        
    print("Ensuring database is set up...")
    run_tests()
