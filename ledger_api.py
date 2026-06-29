import sqlite3
import json
import uuid
from datetime import datetime

# Initialize an in-memory database to simulate a robust banking backend
conn = sqlite3.connect(":memory:")
cursor = conn.cursor()

# 1. IMMUTABILITY: Ledger entries can only be appended (INSERT). No UPDATE or DELETE.
cursor.execute("""
CREATE TABLE ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    amount REAL NOT NULL, -- Negative for debit (money out), Positive for credit (money in)
    description TEXT,
    created_at TEXT NOT NULL
)
""")

# 2. IDEMPOTENCY: To prevent duplicate processing of the same API request
cursor.execute("""
CREATE TABLE idempotency_keys (
    key TEXT PRIMARY KEY,
    response_payload TEXT NOT NULL,
    created_at TEXT NOT NULL
)
""")
conn.commit()

def get_balance(account_id: str) -> float:
    """Calculates balance dynamically from the immutable ledger (Source of Truth)."""
    cursor.execute("SELECT SUM(amount) FROM ledger WHERE account_id = ?", (account_id,))
    result = cursor.fetchone()[0]
    return result if result is not None else 0.0

def execute_transfer(idempotency_key: str, from_acc: str, to_acc: str, amount: float) -> dict:
    """Executes a double-entry transaction with strict idempotency and ACID safety."""
    if amount <= 0:
        return {"status": "error", "message": "Amount must be positive"}

    # Check Idempotency first
    cursor.execute("SELECT response_payload FROM idempotency_keys WHERE key = ?", (idempotency_key,))
    existing = cursor.fetchone()
    if existing:
        print(f"[Idempotency] Duplicate request detected for key: {idempotency_key}. Returning cached response.")
        return json.loads(existing[0])

    # Start ACID transaction
    try:
        # Verify sender balance
        sender_balance = get_balance(from_acc)
        if sender_balance < amount:
            raise ValueError(f"Insufficient funds in {from_acc}. Current balance: {sender_balance}")

        tx_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat()

        # DOUBLE-ENTRY BOOKKEEPING: Every movement must balance to zero.
        # Debit sender (Negative)
        cursor.execute("""
            INSERT INTO ledger (transaction_id, account_id, amount, description, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (tx_id, from_acc, -amount, f"Transfer to {to_acc}", timestamp))

        # Credit receiver (Positive)
        cursor.execute("""
            INSERT INTO ledger (transaction_id, account_id, amount, description, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (tx_id, to_acc, amount, f"Transfer from {from_acc}", timestamp))

        # Prepare response
        response = {
            "status": "success",
            "transaction_id": tx_id,
            "from_account": from_acc,
            "to_account": to_acc,
            "amount": amount,
            "timestamp": timestamp
        }

        # Save idempotency key with response
        cursor.execute("""
            INSERT INTO idempotency_keys (key, response_payload, created_at)
            VALUES (?, ?, ?)
        """, (idempotency_key, json.dumps(response), timestamp))

        conn.commit()
        return response

    except Exception as e:
        conn.rollback()
        error_response = {"status": "failed", "reason": str(e)}
        return error_response

# --- DEMONSTRATION OF THE SYSTEM IN ACTION ---
if __name__ == "__main__":
    print("=== Initializing Accounts with Starting Balances ===")
    # We inject initial capital into Alice's account via a system deposit
    init_tx = str(uuid.uuid4())
    cursor.execute("INSERT INTO ledger (transaction_id, account_id, amount, description, created_at) VALUES (?, ?, ?, ?, ?)",
                   (init_tx, "Alice", 1000.0, "Initial Deposit", datetime.utcnow().isoformat()))
    conn.commit()

    print(f"Alice's Starting Balance: ${get_balance('Alice')}")
    print(f"Bob's Starting Balance: ${get_balance('Bob')}\n")

    # Scenario 1: Successful Transfer
    print("--- Scenario 1: Alice transfers $300 to Bob ---")
    req_key_1 = "req_id_10001"
    res1 = execute_transfer(req_key_1, "Alice", "Bob", 300.0)
    print("API Response:", json.dumps(res1, indent=2))
    print(f"Alice's Balance: ${get_balance('Alice')}")
    print(f"Bob's Balance: ${get_balance('Bob')}\n")

    # Scenario 2: Idempotency Protection (Network retry simulation)
    print("--- Scenario 2: Client retries the exact same request due to network timeout ---")
    res2 = execute_transfer(req_key_1, "Alice", "Bob", 300.0)
    print("API Response:", json.dumps(res2, indent=2))
    print(f"Alice's Balance: ${get_balance('Alice')} (Remained unchanged, safety guaranteed!)")
    print(f"Bob's Balance: ${get_balance('Bob')}\n")

    # Scenario 3: Insufficient Funds Prevention
    print("--- Scenario 3: Alice attempts to transfer $800 (More than her remaining $700) ---")
    req_key_2 = "req_id_10002"
    res3 = execute_transfer(req_key_2, "Alice", "Bob", 800.0)
    print("API Response:", json.dumps(res3, indent=2))
    print(f"Alice's Balance: ${get_balance('Alice')}\n")

    # Scenario 4: Immutability & Correction (No updates allowed!)
    print("--- Scenario 4: Correcting a mistake (Alice wants $100 back) ---")
    print("Instead of 'UPDATING' the ledger, we append a new correction transaction (Reversal).")
    req_key_3 = "req_id_10003"
    res4 = execute_transfer(req_key_3, "Bob", "Alice", 100.0)
    print("API Response:", json.dumps(res4, indent=2))
    print(f"Alice's Final Balance: ${get_balance('Alice')}")
    print(f"Bob's Final Balance: ${get_balance('Bob')}\n")

    # Print entire ledger to show audit trail
    print("=== Full Immutable Audit Trail (Ledger Table) ===")
    cursor.execute("SELECT id, transaction_id, account_id, amount, description FROM ledger")
    for row in cursor.fetchall():
        print(f"ID: {row[0]} | Tx: {row[1][:8]}... | Account: {row[2]:<5} | Amount: {row[3]:>6} | Desc: {row[4]}")
