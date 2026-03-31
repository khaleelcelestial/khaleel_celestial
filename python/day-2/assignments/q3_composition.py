#Q3. Composition — Order with Address and Payment 
# ─── ADDRESS CLASS ────────────────────────────────────
class Address:
    def __init__(self, city, zip_code):
        self.city     = city
        self.zip_code = zip_code

    def display(self):
        return f"{self.city} - {self.zip_code}"


# ─── PAYMENT CLASS ────────────────────────────────────
class PaymentInfo:
    def __init__(self, method, amount):
        self.method = method
        self.amount = amount

    def display(self):
        return f"{self.method}"


# ─── ORDER ITEM CLASS ─────────────────────────────────
class OrderItem:
    def __init__(self, name, qty, price):
        self.name  = name
        self.qty   = qty
        self.price = price

    def total(self):
        return self.qty * self.price          # qty * price

    def display(self):
        return f"{self.name} x{self.qty} = {self.total()}"


# ─── ORDER CLASS (COMPOSITION) ────────────────────────
class Order:
    def __init__(self, address, payment, items):
        self.address = address                # Address object
        self.payment = payment                # PaymentInfo object
        self.items   = items                  # list of OrderItems

    def order_summary(self):
        # shipping
        print(f"Shipping: {self.address.display()}")

        # items
        items_display = ", ".join([item.display() for item in self.items])
        print(f"Items: {items_display}")

        # total
        total = sum([item.total() for item in self.items])
        print(f"Total: {total}")

        # payment
        print(f"Payment: {self.payment.display()}")


# ─── RUNNING THE CODE ─────────────────────────────────
addr  = Address("Bangalore", "560001")
pay   = PaymentInfo("UPI", 1500)
items = [OrderItem("Book", 2, 500), OrderItem("Pen", 5, 100)]
order = Order(addr, pay, items)

order.order_summary()