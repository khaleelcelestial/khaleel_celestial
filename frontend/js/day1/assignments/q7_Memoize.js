function memoize(fn) {
  const cache = {};

  return function (arg) {
    const key = JSON.stringify(arg);

    if (cache[key]) {
      console.log(`Cache hit: ${arg}`);
      return cache[key];
    }

    console.log(`Computing: ${arg}`);
    const result = fn(arg);
    cache[key] = result;
    return result;
  };
}

// Test
const slowSquare = (n) => {
  for (let i = 0; i < 1e7; i++) {}
  return n * n;
};

const fastSquare = memoize(slowSquare);

console.log(fastSquare(5));
console.log(fastSquare(5));
console.log(fastSquare(10));