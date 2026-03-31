#Group strings that are anagrams of each other.
def group_anagram(words):
  ans = {}
  for word in words:
    sorted_word = ''.join(set(sorted(word.lower())))
    if sorted_word not in ans:
      ans[sorted_word] = []
    ans[sorted_word].append(word)
  return list(ans.values())
group_anagram(["eat", "tea", "tan", "ate", "nat", "bat"])