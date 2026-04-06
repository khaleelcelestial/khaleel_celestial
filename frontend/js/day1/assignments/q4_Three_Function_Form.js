// Hoisting demo
console.log(calcDecl([500, 300, 200], 0.18, 50, 30));

// Declaration
function calcDecl(prices, taxRate = 0.18, ...discounts) {
  let total = prices.reduce((a, b) => a + b, 0);
  discounts.forEach(d => (total -= d));
  total = Math.max(0, total);
  return total * (1 + taxRate);
}

// Expression
const calcExpr = function (prices, taxRate = 0.18, ...discounts) {
  let total = prices.reduce((a, b) => a + b, 0);
  discounts.forEach(d => (total -= d));
  total = Math.max(0, total);
  return total * (1 + taxRate);
};

// Arrow
const calcArrow = (prices, taxRate = 0.18, ...discounts) => {
  let total = prices.reduce((a, b) => a + b, 0);
  discounts.forEach(d => (total -= d));
  total = Math.max(0, total);
  return total * (1 + taxRate);
};

// Test
const prices = [500, 300, 200];
console.log(calcExpr(prices, 0.18, 50, 30));
console.log(calcArrow(prices));
console.log(calcArrow(prices, 0.05));