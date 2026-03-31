#E-Commerce Checkout System
from abc import ABC, abstractmethod
class PaymentMethod(ABC):
    @abstractmethod
    def pay(self, amount):
        pass
class UPI(PaymentMethod):
    def pay(self, amount):
        return f"Payment Successful via UPI"

class Card(PaymentMethod):
    def pay(self, amount):
        return f"Payment Successful via Card"
class DiscountStrategy(ABC):
    @abstractmethod
    def apply(self, amount):
        pass
class FestivalDiscount(DiscountStrategy):
    def apply(self, amount):
        return amount * 0.9   # 10% off

class PremiumDiscount(DiscountStrategy):
    def apply(self, amount):
        return amount * 0.8   # 20% off
class Logger:
    def log(self, message):
        print(f"[LOG]: {message}")
class Checkout:
    def __init__(self, payment: PaymentMethod, discount: DiscountStrategy):
        self.payment = payment
        self.discount = discount
        self.logger = Logger()

    def process(self, amount):
        # Apply discount
        final_amount = self.discount.apply(amount)
        self.logger.log(f"Final Amount: {final_amount}")

        # Process payment
        result = self.payment.pay(final_amount)
        self.logger.log(result)

        print(f"Final Amount: {int(final_amount)}")
        print(result)
checkout = Checkout(payment=UPI(), discount=FestivalDiscount())
checkout.process(1000)