#Sort List of Dictionaries using Lambda
data = [{'name':'A','age':30}, {'name':'B','age':20}]
sorted_data = sorted(data, key=lambda x: x['age'])
print(sorted_data)