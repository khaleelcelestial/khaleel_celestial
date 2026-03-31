#Extract Words Starting with Vowel
text = "apple banana orange grape"
result = [word for word in text.split() if word[0].lower() in "aeiou"]
print(result)