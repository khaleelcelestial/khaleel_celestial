#Remove Duplicates While Preserving Order
def remove_dup(nums):
  seen = set()
  res = []
  for num in nums:
    if num not in seen:
      seen.add(num)
      res.append(num)
  return res
remove_dup([4,2,5,7,3,3,4,6,2,4,5,2,2])
