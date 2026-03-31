class TaskNotFoundError(Exception):
    def __init__(self, task_id: int):
        self.task_id = task_id
        super().__init__(f"Task with id={task_id} not found.")


class UserNotFoundError(Exception):
    def __init__(self, identifier):
        self.identifier = identifier
        super().__init__(f"User '{identifier}' not found.")


class DuplicateUserError(Exception):
    def __init__(self, field: str, value: str):
        self.field = field
        self.value = value
        super().__init__(f"A user with {field}='{value}' already exists.")
