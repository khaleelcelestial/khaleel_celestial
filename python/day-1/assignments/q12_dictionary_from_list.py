# Create Dictionary from Two Lists
keys = ['a','b','c']
values = [1,2,3]
result = {k: v for k, v in zip(keys, values)}
print(result)