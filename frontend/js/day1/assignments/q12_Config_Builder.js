function deepFreeze(obj) {
  Object.freeze(obj);
  Object.values(obj).forEach(val => {
    if (typeof val === "object" && val !== null && !Object.isFrozen(val)) {
      deepFreeze(val);
    }
  });
  return obj;
}

function createConfig({
  server: { port = 3000, host = "localhost" } = {},
  database: {
    url = "postgres://localhost:5432/mydb",
    poolSize = 5,
  } = {},
  logging: { level = "info", file = "app.log" } = {},
} = {}) {
  const config = {
    server: { port, host },
    database: { url, poolSize },
    logging: { level, file },
  };

  return deepFreeze(config);
}

// Test
const config1 = createConfig({
  server: { port: 9090 },
  logging: { level: "debug" },
});

const config2 = createConfig({});

config2.server.port = 9999;

console.log(config1);
console.log(config2.server.port);