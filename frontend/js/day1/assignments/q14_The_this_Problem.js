const timer = {
  seconds: 0,
  intervalId: null,

  // Fix 1: bind(this)
  startBind: function () {
    this.intervalId = setInterval(function () {
      this.seconds++;
      console.log(`Bind: ${this.seconds}s`);
    }.bind(this), 1000);
  },

  // Fix 2: arrow function
  startArrow: function () {
    this.intervalId = setInterval(() => {
      this.seconds++;
      console.log(`Arrow: ${this.seconds}s`);
    }, 1000);
  },

  // Fix 3: self = this
  startSelf: function () {
    const self = this;
    this.intervalId = setInterval(function () {
      self.seconds++;
      console.log(`Self: ${self.seconds}s`);
    }, 1000);
  },

  stop: function () {
    clearInterval(this.intervalId);
  },
};

// Test
// timer.startBind();
// timer.startArrow();
// timer.startSelf();