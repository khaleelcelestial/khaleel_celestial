function deepEqual(a, b) {
  if (a === b) return true;

  if (a === null || b === null) return a === b;

  if (typeof a !== "object" || typeof b !== "object") return false;

  if (Array.isArray(a) !== Array.isArray(b)) return false;

  const keysA = Object.keys(a);
  const keysB = Object.keys(b);

  if (keysA.length !== keysB.length) return false;

  for (let key of keysA) {
    if (!deepEqual(a[key], b[key])) return false;
  }

  return true;
}

// Test
console.log(deepEqual({ a: 1, b: { c: 2 } }, { a: 1, b: { c: 2 } }));
console.log(deepEqual([1, [2, 3]], [1, [2, 3]]));
console.log(deepEqual({ a: 1 }, { a: "1" }));
console.log(deepEqual(null, null));
console.log(deepEqual(null, {}));