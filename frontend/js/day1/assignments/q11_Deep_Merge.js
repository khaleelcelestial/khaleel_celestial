function deepMerge(target, source) {
  if (typeof target !== "object" || target === null) return source;
  if (typeof source !== "object" || source === null) return source;

  const result = { ...target };

  for (let key in source) {
    if (Array.isArray(source[key]) && Array.isArray(target[key])) {
      result[key] = [...target[key], ...source[key]];
    } else if (
      typeof source[key] === "object" &&
      source[key] !== null &&
      typeof target[key] === "object" &&
      target[key] !== null
    ) {
      result[key] = deepMerge(target[key], source[key]);
    } else {
      result[key] = source[key];
    }
  }

  return result;
}

// Test
const defaults = {
  server: { port: 3000, host: "localhost" },
  database: { url: "localhost:5432", pool: { min: 2, max: 5 } },
  features: ["auth"],
};

const overrides = {
  server: { port: 8080 },
  database: { pool: { max: 20 } },
  features: ["logging"],
  debug: true,
};

console.log(deepMerge(defaults, overrides));