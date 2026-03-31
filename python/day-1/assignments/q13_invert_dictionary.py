#Invert Dictionary Using Comprehension
data = {'a':1, 'b':2, 'c':3}
inverted = {v: k for k, v in data.items()}
print(inverted)