from fastapi import FastAPI
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import List

app = FastAPI()

# =========================
# 1. USER SCHEMAS
# =========================

class UserCreate(BaseModel):
    email: EmailStr
    age: int = Field(gt=0, lt=100)   # age between 1 and 99
    password: str = Field(min_length=6)

    # Custom validator (business rule)
    @field_validator("password")
    def password_must_have_number(cls, value):
        if not any(char.isdigit() for char in value):
            raise ValueError("Password must contain at least one number")
        return value


class UserResponse(BaseModel):
    id: int
    email: EmailStr
    age: int


# =========================
# 2. NESTED ORDER SCHEMAS
# =========================

class OrderItem(BaseModel):
    product_name: str = Field(min_length=2)
    quantity: int = Field(gt=0)
    price: float = Field(gt=0)


class Order(BaseModel):
    user_id: int
    items: List[OrderItem]

    # Custom validator (business rule)
    @field_validator("items")
    def order_must_not_be_empty(cls, value):
        if len(value) == 0:
            raise ValueError("Order must have at least one item")
        return value


# =========================
# 3. DUMMY STORAGE
# =========================

users = []
orders = []
user_id_counter = 1


# =========================
# 4. CREATE USER
# =========================

@app.post("/users", response_model=UserResponse)
def create_user(user: UserCreate):
    global user_id_counter

    new_user = {
        "id": user_id_counter,
        "email": user.email,
        "age": user.age
    }

    users.append(new_user)
    user_id_counter += 1

    return new_user


# =========================
# 5. CREATE ORDER
# =========================

@app.post("/orders")
def create_order(order: Order):
    orders.append(order.model_dump())
    return {
        "status": "success",
        "data": order
    }