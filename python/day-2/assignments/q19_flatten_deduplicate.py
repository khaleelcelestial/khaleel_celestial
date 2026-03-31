articles = [
    {"title": "AI Intro", "tags": ["python", "ml", "ai"]},
    {"title": "Web Dev",  "tags": ["python", "fastapi", "api"]},
    {"title": "Data 101", "tags": ["ml", "pandas", "python"]},
]

# ─── 2 LINES ──────────────────────────────────────────
unique_tags = {tag for article in articles for tag in article["tags"]}
result      = sorted(unique_tags)

print(result)