#Replace Negative Numbers with 0
nums = [1, -2, 3, -4, 5]
result = [x if x >= 0 else 0 for x in nums]
print(result)