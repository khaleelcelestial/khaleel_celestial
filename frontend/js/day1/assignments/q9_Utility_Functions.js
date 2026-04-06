const groupBy = (arr, key) =>
  arr.reduce((acc, obj) => {
    const k = obj[key];
    acc[k] = acc[k] || [];
    acc[k].push(obj);
    return acc;
  }, {});

const unique = (arr) =>
  arr.filter((v, i) => arr.indexOf(v) === i);

const chunk = (arr, size) =>
  arr.reduce((acc, val, i) => {
    if (i % size === 0) acc.push([]);
    acc[acc.length - 1].push(val);
    return acc;
  }, []);

const zip = (arr1, arr2) =>
  arr1.map((v, i) => [v, arr2[i]]);

// Test
const people = [
  { name: "Alice", dept: "Engineering" },
  { name: "Bob", dept: "Marketing" },
  { name: "Carol", dept: "Engineering" },
  { name: "Dave", dept: "Marketing" },
  { name: "Eve", dept: "HR" },
];

console.log(groupBy(people, "dept"));
console.log(unique([1, 2, 2, 3, 4, 4, 5]));
console.log(chunk([1, 2, 3, 4, 5, 6, 7], 3));
console.log(zip(["a", "b", "c"], [1, 2, 3]));