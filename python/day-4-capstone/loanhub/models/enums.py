import enum


class UserRole(str, enum.Enum):
    user = "user"
    admin = "admin"


class LoanPurpose(str, enum.Enum):
    personal = "personal"
    education = "education"
    home = "home"
    vehicle = "vehicle"
    business = "business"


class EmploymentStatus(str, enum.Enum):
    employed = "employed"
    self_employed = "self_employed"
    unemployed = "unemployed"
    student = "student"


class LoanStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
