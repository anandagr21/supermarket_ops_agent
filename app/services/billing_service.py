from typing import Optional
from sqlmodel import Session, select
from app.models import Bill, BillItem, KhataTransaction, Product
from app.repositories.bill_repository import BillRepository
from app.repositories.product_repository import ProductRepository
from app.repositories.khata_repository import KhataRepository

class BillingService:
    def __init__(self, session: Session):
        self.session = session
        self.bill_repo = BillRepository(session)
        self.product_repo = ProductRepository(session)
        self.khata_repo = KhataRepository(session)
        
    def add_to_bill(self, chat_id: int, product_name: str, quantity: float) -> str:
        product_name = product_name.lower().strip()
        product = self.product_repo.get_by_name(chat_id, product_name)
        
        if not product:
            return f"Product '{product_name}' not found in inventory."
            
        bill = self.bill_repo.get_draft_bill(chat_id)
        if not bill:
            bill = Bill(chat_id=chat_id, status="draft")
            self.session.add(bill)
            self.session.commit()
            self.session.refresh(bill)
            
        bill_item = self.bill_repo.get_bill_item(bill.id, product.id)
        
        if bill_item:
            bill_item.quantity += quantity
        else:
            bill_item = BillItem(
                bill_id=bill.id,
                product_id=product.id,
                quantity=quantity,
                unit_price=product.mrp,
                gst_amount=0.0,
                total_price=0.0
            )
        
        self.session.add(bill_item)
        self.session.commit()
        return f"Added {quantity} {product.unit} of {product_name} to the bill."

    def finalize_bill(self, chat_id: int, payment_mode: str, khata_customer_name: Optional[str] = None) -> str:
        """
        Finalize the draft bill, decrement stock, and calculate taxes.
        Oversell guard is enforced here.
        """
        bill = self.bill_repo.get_draft_bill(chat_id)
        if not bill:
            return "No active draft bill to finalize."
            
        items = self.bill_repo.get_all_bill_items(bill.id)
        if not items:
            return "Bill is empty."
            
        payment_mode = payment_mode.lower()
        if payment_mode not in ["cash", "upi", "card", "khata"]:
            return f"Invalid payment mode: {payment_mode}"
            
        total_amount = 0.0
        total_tax = 0.0
        total_cgst = 0.0
        total_sgst = 0.0

        for item in items:
            # Lock the product row to prevent concurrent stock operations
            product = self.session.exec(
                select(Product)
                .where(Product.id == item.product_id)
                .with_for_update()
            ).one()

            # Oversell Guard
            if product.stock_quantity < item.quantity:
                self.session.rollback()
                return f"OVERSELL PREVENTED: Cannot sell {item.quantity} of {product.name}. Only {product.stock_quantity} left in stock."

            product.stock_quantity -= item.quantity

            # Tax math: MRP is tax-inclusive, so back-calculate base price.
            # CGST and SGST are computed independently from the base —
            # same base × same rate guarantees they are always equal.
            item.total_price = item.quantity * product.mrp
            gst_rate = product.gst_slab_percent / 100.0
            half_rate = gst_rate / 2.0
            base_price = item.total_price / (1 + gst_rate)

            cgst = round(base_price * half_rate)
            sgst = round(base_price * half_rate)
            total_gst = cgst + sgst

            item.gst_amount = total_gst
            item.cgst_amount = cgst
            item.sgst_amount = sgst

            total_amount += item.total_price
            total_tax += total_gst
            total_cgst += cgst
            total_sgst += sgst

            self.session.add(product)
            self.session.add(item)

        bill.total_amount = round(total_amount, 2)
        bill.total_tax = total_tax
        bill.total_cgst = total_cgst
        bill.total_sgst = total_sgst
        bill.payment_mode = payment_mode
        bill.status = "finalized"
        self.session.add(bill)
        
        # Khata Logic
        if payment_mode == "khata":
            if not khata_customer_name:
                self.session.rollback()
                return "Khata payment mode requires a customer name."
                
            khata_customer_name = khata_customer_name.lower().strip()
            account = self.khata_repo.get_account(chat_id, khata_customer_name)
            if not account:
                self.session.rollback()
                return f"Khata account for '{khata_customer_name}' does not exist. Please create it first."
                
            account.balance += bill.total_amount
            txn = KhataTransaction(
                account_id=account.id,
                amount=bill.total_amount,
                transaction_type="purchase_on_credit",
                reference=f"Bill #{bill.id}"
            )
            self.session.add(account)
            self.session.add(txn)
            
        try:
            self.session.commit()
        except Exception as e:
            self.session.rollback()
            return f"Database error during finalization: {str(e)}"
            
        return f"Bill finalized successfully! Total: ₹{bill.total_amount} (CGST: ₹{bill.total_cgst}, SGST: ₹{bill.total_sgst}). Payment: {payment_mode}."

    def query_todays_sales(self, chat_id: int) -> str:
        from datetime import date
        from collections import defaultdict

        today = date.today()
        statement = select(Bill).where(
            Bill.chat_id == chat_id,
            Bill.status == "finalized"
        )
        bills = self.session.exec(statement).all()

        todays_bills = [b for b in bills if b.timestamp.date() == today]

        if not todays_bills:
            return "There are no finalized sales for today."

        total_revenue = sum(b.total_amount for b in todays_bills)
        total_tax = sum(b.total_tax for b in todays_bills)
        total_cgst = sum(b.total_cgst for b in todays_bills)
        total_sgst = sum(b.total_sgst for b in todays_bills)

        # Payment mode breakdown
        mode_totals = defaultdict(float)
        mode_counts = defaultdict(int)
        for b in todays_bills:
            mode = b.payment_mode or "unknown"
            mode_totals[mode] += b.total_amount
            mode_counts[mode] += 1
        payment_lines = "\n".join(
            f"  {mode}: {cnt} bills, ₹{amt:,.0f}" for mode, amt, cnt in
            sorted([(m, mode_totals[m], mode_counts[m]) for m in mode_totals], key=lambda x: -x[1])
        )

        # Top items by quantity sold today
        bill_ids = [b.id for b in todays_bills]
        item_rows = self.session.exec(
            select(BillItem).where(BillItem.bill_id.in_(bill_ids))
        ).all()

        product_totals = defaultdict(lambda: {"qty": 0.0, "revenue": 0.0})
        for item in item_rows:
            product = self.session.get(Product, item.product_id)
            name = product.name if product else f"product_{item.product_id}"
            product_totals[name]["qty"] += item.quantity
            product_totals[name]["revenue"] += item.total_price

        top_items = sorted(product_totals.items(), key=lambda x: -x[1]["qty"])[:5]
        top_lines = "\n".join(
            f"  {i+1}. {name}: {d['qty']:.0f} sold, ₹{d['revenue']:,.0f}"
            for i, (name, d) in enumerate(top_items)
        )

        return (
            f"Today's Sales Summary:\n"
            f"Total Revenue: ₹{total_revenue:,.0f}\n"
            f"Total Tax: ₹{total_tax} (CGST: ₹{total_cgst}, SGST: ₹{total_sgst})\n"
            f"Bills: {len(todays_bills)}\n\n"
            f"By Payment Mode:\n{payment_lines}\n\n"
            f"Top Items:\n{top_lines}"
        )
