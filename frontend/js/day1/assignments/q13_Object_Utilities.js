const pick = (obj, keys) =>
  Object.entries(obj).reduce((acc, [k, v]) => {
    if (keys.includes(k)) acc[k] = v;
    return acc;
  }, {});

const omit = (obj, keys) =>
  Object.entries(obj).reduce((acc, [k, v]) => {
    if (!keys.includes(k)) acc[k] = v;
    return acc;
  }, {});

const mapKeys = (obj, fn) =>
  Object.entries(obj).reduce((acc, [k, v]) => {
    acc[fn(k)] = v;
    return acc;
  }, {});

const mapValues = (obj, fn) =>
  Object.entries(obj).reduce((acc, [k, v]) => {
    acc[k] = fn(v);
    return acc;
  }, {});

// Test
const user = {
  firstName: "Alice",
  lastName: "Smith",
  age: 25,
  password: "secret",
};

console.log(pick(user, ["firstName", "lastName"]));
console.log(omit(user, ["password"]));
console.log(mapKeys(user, k => k.toUpperCase()));
console.log(mapValues({ a: 1, b: 2, c: 3 }, v => v * 10));