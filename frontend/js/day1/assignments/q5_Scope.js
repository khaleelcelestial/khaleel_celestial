// Fix 1: let (block scope)
for (let i = 0; i < 5; i++) {
  setTimeout(() => console.log(i), i * 1000);
}

// Fix 2: IIFE (captures var)
for (var i = 0; i < 5; i++) {
  (function (j) {
    setTimeout(() => console.log(j), j * 1000);
  })(i);
}