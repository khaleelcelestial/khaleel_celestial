#Smart Banking System (OOP + SRP + Encapsulation)
# Transaction Logger (SRP)
class TransactionLogger:
    def log(self, message):
        print(f"[LOG]: {message}")
# Base Account Class
class Account:
    def __init__(self, name, balance):
        self.name = name
        self.__balance = balance   # private variable
        self.logger = TransactionLogger()
    # Deposit
    def deposit(self, amount):
        if amount <= 0:
            print("Invalid deposit amount")
            return
        self.__balance += amount
        self.logger.log(f"{amount} deposited")
    # Withdraw
    def withdraw(self, amount):
        if amount <= 0:
            print("Invalid withdrawal amount")
            return
        if amount > self.__balance:
            print("Insufficient balance")
            return
        self.__balance -= amount
        self.logger.log(f"{amount} withdrawn")
    # Get Balance
    def get_balance(self):
        return f"Balance: {self.__balance}"
# SavingsAccount (can extend later)
class SavingsAccount(Account):
    pass

acc = SavingsAccount("John", 1000)

acc.deposit(500)
acc.withdraw(200)

print(acc.get_balance())