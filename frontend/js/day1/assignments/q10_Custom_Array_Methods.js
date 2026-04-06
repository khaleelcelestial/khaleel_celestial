Array.prototype.myMap = function (cb) {
  const res = [];
  for (let i = 0; i < this.length; i++) {
    res.push(cb(this[i], i, this));
  }
  return res;
};

Array.prototype.myFilter = function (cb) {
  const res = [];
  for (let i = 0; i < this.length; i++) {
    if (cb(this[i], i, this)) res.push(this[i]);
  }
  return res;
};

Array.prototype.myReduce = function (cb, init) {
  let acc = init;
  let start = 0;

  if (acc === undefined) {
    acc = this[0];
    start = 1;
  }

  for (let i = start; i < this.length; i++) {
    acc = cb(acc, this[i], i, this);
  }

  return acc;
};

// Test
const nums = [1, 2, 3, 4, 5];

console.log(nums.myMap(n => n * 2));
console.log(nums.myFilter(n => n % 2 === 0));
console.log(nums.myReduce((a, b) => a + b, 0));