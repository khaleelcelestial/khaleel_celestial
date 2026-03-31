#Second Largest Unique Element
def second_largest(nums):
    first = second = float('-inf')
    for num in nums:
        if num == first:
            continue
        if num > first:
            second = first
            first = num
        elif num > second:
            second = num
    return second