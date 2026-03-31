#Flatten Nested List (Recursive)
def flatten(lst):
    result = []
    for item in lst:
        if isinstance(item, list):
            result.extend(flatten(item))  # recursive call
        else:
            result.append(item)
    return result
flatten([1, [2, [3, 4], 5], 6, 7])