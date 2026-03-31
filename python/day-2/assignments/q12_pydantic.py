#Q12. Pydantic — User Schema with Nested Validation 
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional


# ─── ADDRESS MODEL ────────────────────────────────────
class Address(BaseModel):
    street  : str
    city    : str
    zip_code: str = Field(min_length=6, max_length=6)  # exactly 6 chars

    @field_validator("zip_code")
    def zip_must_be_digits(cls, value):
        if not value.isdigit():                        # must be numbers only
            raise ValueError("zip_code must contain only digits")
        return value


# ─── USER CREATE ──────────────────────────────────────
class UserCreate(BaseModel):
    username: str
    email   : str
    password: str  = Field(min_length=8)               # min 8 chars
    age     : int  = Field(ge=18, le=120)              # 18 to 120
    address : Address                                   # nested model ✅

    @field_validator("email")
    def email_must_have_at(cls, value):
        if "@" not in value:
            raise ValueError("email must contain @")
        return value


# ─── USER RESPONSE ────────────────────────────────────
class UserResponse(BaseModel):
    username: str
    email   : str
    age     : int
    address : Address
    # password NOT here → never exposed ✅


# ─── RUNNING THE CODE ─────────────────────────────────
data = {
    "username": "alice",
    "email"   : "alice@mail.com",
    "password": "securepass",
    "age"     : 25,
    "address" : {
        "street"  : "MG Road",
        "city"    : "Bangalore",
        "zip_code": "560001"
    }
}

# create and validate
user = UserCreate(**data)

# serialize — model_dump includes all fields
print("\n─── model_dump() ───")
print(user.model_dump())

# response — password excluded
print("\n─── UserResponse ───")
response = UserResponse(**user.model_dump())
print(response)

# show individual fields
print("\n─── Individual Fields ───")
print(f"Username : {response.username}")
print(f"Email    : {response.email}")
print(f"Age      : {response.age}")
print(f"City     : {response.address.city}")
print(f"Zip      : {response.address.zip_code}")