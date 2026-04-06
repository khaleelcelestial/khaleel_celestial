class TaskManager {
  #tasks = [];
  #nextId = 1;

  addTask(title, priority) {
    const task = {
      id: this.#nextId++,
      title,
      priority,
      status: "pending",
      createdAt: new Date().toISOString(),
    };
    this.#tasks.push(task);
  }

  completeTask(id) {
    const task = this.#tasks.find(t => t.id === id);
    if (!task) throw Error(`Task with id ${id} not found`);
    task.status = "completed";
  }

  removeTask(id) {
    const index = this.#tasks.findIndex(t => t.id === id);
    if (index === -1) throw Error(`Task with id ${id} not found`);
    this.#tasks.splice(index, 1);
  }

  getTasks(status) {
    return status
      ? this.#tasks.filter(t => t.status === status)
      : this.#tasks;
  }

  get taskCount() {
    return this.#tasks.length;
  }

  static fromJSON(json) {
    const data = JSON.parse(json);
    const tm = new TaskManager();
    data.forEach(d => tm.addTask(d.title, d.priority));
    return tm;
  }
}

// Test
const tm = TaskManager.fromJSON('[{"title":"Setup env","priority":"high"}]');
tm.addTask("Write tests", "medium");
tm.addTask("Deploy app", "low");
tm.completeTask(1);

console.log(tm.taskCount);
console.log(tm.getTasks("pending"));
console.log(tm.getTasks());