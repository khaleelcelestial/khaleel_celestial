const delay = ms => new Promise(res => setTimeout(res, ms));

async function getOrderTotal(id) {
  try {
    const user = await fetchUser(id);
    const order = await fetchOrders(user.id);
    const total = await fetchOrderTotal(order.orderId);
    console.log(`User: ${user.name} | Order: ${order.orderId} | Total: ${total.total}`);
  } catch (e) {
    console.error(e.message);
  }
}

// Sequential
async function fetchUsersSequential(ids) {
  for (let id of ids) {
    const user = await fetchUser(id);
    console.log(`Fetched user ${id}: ${user.name}`);
  }
}

// Concurrent
async function fetchUsersConcurrent(ids) {
  const users = await Promise.all(ids.map(fetchUser));
  console.log("All:", users.map(u => u.name));
}

// Retry
async function fetchWithRetry(id, maxRetries) {
  for (let i = 1; i <= maxRetries; i++) {
    try {
      return await fetchUser(id);
    } catch (e) {
      console.log(`[retry] Attempt ${i} failed: ${e.message}`);
      if (i === maxRetries) {
        throw new Error(`All ${maxRetries} attempts failed for userId ${id}`);
      }
      await delay(500);
    }
  }
}

// Test
getOrderTotal(1);
fetchUsersSequential([1, 2, 3]);
fetchUsersConcurrent([1, 2, 3]);
fetchWithRetry(10, 3).catch(console.error);