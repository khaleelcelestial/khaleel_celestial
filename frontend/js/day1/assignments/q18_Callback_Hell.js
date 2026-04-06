function fetchUser(id, cb) {
  setTimeout(() => {
    if (id > 5) return cb(new Error("User not found"));
    console.log(`Fetching user ${id}...`);
    cb(null, { id, name: "alice" });
  }, 1000);
}

function fetchOrders(userId, cb) {
  setTimeout(() => {
    console.log(`Fetching orders for user ${userId}...`);
    cb(null, { orderId: "ORD-101" });
  }, 1000);
}

function fetchOrderTotal(orderId, cb) {
  setTimeout(() => {
    console.log(`Fetching total for ${orderId}...`);
    cb(null, { total: 2500 });
  }, 1000);
}

// Callback Hell
fetchUser(1, (err, user) => {
  if (err) return console.error(err);

  fetchOrders(user.id, (err, order) => {
    if (err) return console.error(err);

    fetchOrderTotal(order.orderId, (err, total) => {
      if (err) return console.error(err);

      console.log({ user: user.name, orderId: order.orderId, total: total.total });
    });
  });
});

// Refactor
function handleUser(err, user) {
  if (err) return console.error(err);
  fetchOrders(user.id, handleOrders.bind(null, user));
}

function handleOrders(user, err, order) {
  if (err) return console.error(err);
  fetchOrderTotal(order.orderId, handleTotal.bind(null, user, order));
}

function handleTotal(user, order, err, total) {
  if (err) return console.error(err);
  console.log({ user: user.name, orderId: order.orderId, total: total.total });
}

// Test
fetchUser(1, handleUser);