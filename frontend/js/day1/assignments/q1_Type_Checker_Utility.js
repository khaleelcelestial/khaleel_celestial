function describeType(value) {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  if (Number.isNaN(value)) return "NaN";

  return typeof value;
}

// Test
console.log(describeType(42));
console.log(describeType("hello"));
console.log(describeType(null));
console.log(describeType([1, 2]));
console.log(describeType(NaN));
console.log(describeType({ a: 1 }));
console.log(describeType(undefined));
console.log(describeType(() => {}));