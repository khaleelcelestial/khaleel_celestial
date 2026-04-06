function createWallet(ownerName, initialBalance) {
  let balance = initialBalance;
  let history = [];

  return {
    deposit(amount) {
      if (amount <= 0) throw Error("Invalid deposit");
      balance += amount;
      history.push({ type: "deposit", amount, balance });
    },
    withdraw(amount) {
      if (amount <= 0) throw Error("Invalid withdraw");
      if (amount > balance)
        throw Error(`Insufficient balance. Current balance: ${balance}`);
      balance -= amount;
      history.push({ type: "withdraw", amount, balance });
    },
    getBalance() {
      return balance;
    },
    getOwner() {
      return ownerName;
    },
    getHistory() {
      return history;
    },
  };
}

// Test
const wallet = createWallet("Alice", 1000);
wallet.deposit(500);
wallet.withdraw(200);
console.log(wallet.getBalance());
console.log(wallet.getHistory());
console.log(wallet.getOwner());