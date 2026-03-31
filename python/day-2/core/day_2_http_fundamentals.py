from fastapi import FastAPI
from typing import Optional

app = FastAPI()

tasks = []
task_id = 1

# CREATE
@app.post("/tasks")
def create_task(title: str):
    global task_id
    task = {"id": task_id, "title": title, "completed": False}
    tasks.append(task)
    task_id += 1
    return task

# LIST
@app.get("/tasks")
def get_tasks():
    return tasks

# GET SINGLE
@app.get("/tasks/{id}")
def get_task(id: int):
    for task in tasks:
        if task["id"] == id:
            return task
    return {"error": "Task not found"}

# UPDATE (PUT)
@app.put("/tasks/{id}")
def update_task(id: int, title: str, completed: bool):
    for task in tasks:
        if task["id"] == id:
            task["title"] = title
            task["completed"] = completed
            return task
    return {"error": "Task not found"}

# UPDATE (PATCH)
@app.patch("/tasks/{id}")
def patch_task(id: int, title: Optional[str] = None, completed: Optional[bool] = None):
    for task in tasks:
        if task["id"] == id:
            if title is not None:
                task["title"] = title
            if completed is not None:
                task["completed"] = completed
            return task
    return {"error": "Task not found"}

# DELETE
@app.delete("/tasks/{id}")
def delete_task(id: int):
    for task in tasks:
        if task["id"] == id:
            tasks.remove(task)
            return {"message": "Deleted"}
    return {"error": "Task not found"}