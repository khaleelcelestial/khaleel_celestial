const students = [
  { name: "Alice", scores: [85, 92, 78] },
  { name: "Bob", scores: [45, 55, 60] },
  { name: "Carol", scores: [90, 95, 88] },
  { name: "Dave", scores: [30, 40, 35] },
  { name: "Eve", scores: [72, 68, 75] },
];

const result = students
  .map(s => {
    const avg =
      Math.round(
        (s.scores.reduce((a, b) => a + b, 0) / s.scores.length) * 100
      ) / 100;
    return { name: s.name, average: avg };
  })
  .filter(s => s.average >= 60)
  .sort((a, b) => b.average - a.average)
  .map(s => ({
    ...s,
    grade:
      s.average >= 90
        ? "A"
        : s.average >= 80
        ? "B"
        : s.average >= 70
        ? "C"
        : "D",
  }));

console.log(result);