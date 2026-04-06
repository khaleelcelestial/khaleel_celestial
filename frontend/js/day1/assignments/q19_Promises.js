function fetchUser(id) {
  return new Promise((res, rej) => {
    setTimeout(() => {
      if (id > 5) return rej(new Error("User not found"));
      res({ id, name: "alice" });
    }, 1000);
  });
}

function fetchOrders(userId) {
  return new Promise(res =>
    setTimeout(() => res({ orderId: "ORD-101" }), 1000)
  );
}

function fetchOrderTotal(orderId) {
  return new Promise(res =>
    setTimeout(() => res({ total: 2500 }), 1000)
  );
}

// Chain
fetchUser(1)
  .then(u => fetchOrders(u.id))
  .then(o => fetchOrderTotal(o.orderId))
  .then(r => console.log("Total:", r.total))
  .catch(e => console.error(e.message));

// Concurrent
Promise.all([fetchUser(1), fetchUser(2), fetchUser(3)])
  .then(console.log)
  .catch(console.error);

// allSettled
Promise.allSettled([fetchUser(1), fetchUser(2), fetchUser(10)])
  .then(console.log);

// race
const timeout = new Promise((_, rej) =>
  setTimeout(() => rej(new Error("Timeout")), 500)
);

Promise.race([fetchUser(1), timeout])
  .then(console.log)
  .catch(console.error);