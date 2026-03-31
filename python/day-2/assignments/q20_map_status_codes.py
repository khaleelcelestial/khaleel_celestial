codes = [200, 201, 404, 500, 301, 403, 502, 204]


# ─── HELPER FUNCTION ──────────────────────────────────
def classify(code):
    if code >= 500:   return "server_error"
    if code >= 400:   return "client_error"
    if code >= 300:   return "redirect"
    if code >= 200:   return "success"
    return "unknown"


# ─── SINGLE LIST COMPREHENSION ────────────────────────
result = [(code, classify(code)) for code in codes]

print(result)