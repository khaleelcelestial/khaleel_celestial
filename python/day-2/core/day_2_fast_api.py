from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from typing import Optional
import time

app = FastAPI()

# =========================
# In-memory DB
# =========================
users = []
user_id = 1

# =========================
# 1. Middleware (Logging)
# =========================
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()

    print(f"Request: {request.method} {request.url}")

    response = await call_next(request)

    duration = time.time() - start_time
    print(f"Response: {response.status_code} | Time: {duration:.4f}s")

    return response

# =========================
# 2. Custom Exception
# =========================
class CustomException(Exception):
    def __init__(self, message: str):
        self.message = message

@app.exception_handler(CustomException)
async def custom_exception_handler(request: Request, exc: CustomException):
    return JSONResponse(
        status_code=400,
        content={
            "status": "error",
            "message": exc.message
        }
    )

# =========================
# 3. Health Check
# =========================
@app.get("/health")
def health_check():
    return {"status": "ok"}

# =========================
# 4. CREATE User
# =========================
@app.post("/users")
def create_user(name: str):
    global user_id
    user = {"id": user_id, "name": name}
    users.append(user)
    user_id += 1
    return {
        "status": "success",
        "data": user
    }

# =========================
# 5. GET Users (Filter + Pagination)
# =========================
@app.get("/users")
def get_users(skip: int = 0, limit: int = 10, name: Optional[str] = None):
    result = users

    # Filtering
    if name:
        result = [u for u in result if u["name"] == name]

    # Pagination
    result = result[skip: skip + limit]

    return {
        "status": "success",
        "data": result
    }

# =========================
# 6. GET Single User
# =========================
@app.get("/users/{id}")
def get_user(id: int):
    for user in users:
        if user["id"] == id:
            return {
                "status": "success",
                "data": user
            }
    raise HTTPException(status_code=404, detail="User not found")

# =========================
# 7. UPDATE (PUT - Full)
# =========================
@app.put("/users/{id}")
def update_user(id: int, name: str):
    for user in users:
        if user["id"] == id:
            user["name"] = name
            return {
                "status": "success",
                "data": user
            }
    raise HTTPException(status_code=404, detail="User not found")

# =========================
# 8. UPDATE (PATCH - Partial)
# =========================
@app.patch("/users/{id}")
def patch_user(id: int, name: Optional[str] = None):
    for user in users:
        if user["id"] == id:
            if name is not None:
                user["name"] = name
            return {
                "status": "success",
                "data": user
            }
    raise HTTPException(status_code=404, detail="User not found")

# =========================
# 9. DELETE User
# =========================
@app.delete("/users/{id}")
def delete_user(id: int):
    for user in users:
        if user["id"] == id:
            users.remove(user)
            return {
                "status": "success",
                "message": "User deleted"
            }
    raise HTTPException(status_code=404, detail="User not found")

# =========================
# 10. Trigger Custom Error
# =========================
@app.get("/error")
def trigger_error():
    raise CustomException("This is a custom error!")