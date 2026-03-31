from fastapi import FastAPI
from pydantic import BaseModel, EmailStr, Field

app = FastAPI()

class User(BaseModel):
    email: EmailStr
    age: int = Field(gt=0)

@app.get("/")
def read_root():
    return {"message": "Hello"}

@app.post("/users")
def create_user(user: User):
    return {"email": user.email, "age": user.age}