#Custom Exception Handling
class SalaryTooLowError(Exception):
    pass
def check_salary(salary):
    if salary < 10000:
        raise SalaryTooLowError("SalaryTooLowError")
    return "Salary is valid"
try:
    print(check_salary(8000))
except SalaryTooLowError as e:
    print(e)