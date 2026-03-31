#Q5. OCP — Extensible Discount System 
from abc import ABC, abstractmethod

# ─── ABSTRACT BASE CLASS ──────────────────────────────
# defines the contract all discounts must follow
class Discount(ABC):
    @abstractmethod
    def apply(self, amount):
        pass                          # subclasses MUST implement this


# ─── DISCOUNT 1: NO DISCOUNT ──────────────────────────
class NoDiscount(Discount):
    def apply(self, amount):
        return amount                 # no change, return as is


# ─── DISCOUNT 2: PERCENTAGE DISCOUNT ─────────────────
class PercentageDiscount(Discount):
    def apply(self, amount):
        result = amount - (amount * 0.10)   # 10% off
        return max(0, result)               # minimum 0


# ─── DISCOUNT 3: FLAT DISCOUNT ────────────────────────
class FlatDiscount(Discount):
    def apply(self, amount):
        result = amount - 200               # Rs 200 off
        return max(0, result)               # minimum 0


# ─── DISCOUNT 4: BUY ONE GET ONE FREE ─────────────────
class BuyOneGetOneFree(Discount):
    def apply(self, amount):
        result = amount * 0.50              # 50% off
        return max(0, result)               # minimum 0


# ─── CALCULATE TOTAL ──────────────────────────────────
# never changes, even when new discounts are added
def calculate_total(amount, discount):
    return discount.apply(amount)           # just calls apply()


# ─── RUNNING THE CODE ─────────────────────────────────
print(calculate_total(1000, NoDiscount()))
print(calculate_total(1000, PercentageDiscount()))
print(calculate_total(1000, FlatDiscount()))
print(calculate_total(1000, BuyOneGetOneFree()))