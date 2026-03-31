import json
def is_valid_json(data):
    try:
        json.loads(data)
        return True
    except json.JSONDecodeError:
        return False
print(is_valid_json('{"name": "John", "age": 30}'))
print(is_valid_json('{"name": "John", "age": }'))