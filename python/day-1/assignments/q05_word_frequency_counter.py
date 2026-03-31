#Word Frequency Counter (File Handling)
import re
from collections import Counter
def word_frequency(file_path):
    with open(file_path, "r") as file:
        text = file.read()
    text = text.lower()
    text = re.sub(r'[^\w\s]', '', text)
    words = text.split()
    freq = Counter(words)
    return dict(freq)
word_frequency("replace.txt")