#Q2. Inheritance — Admin and Customer Users
# ─── BASE CLASS ───────────────────────────────────────
class User:
    def __init__(self, username, role):
        self.username = username
        self.role     = role

    def display_profile(self):
        print(f"User: {self.username} | Role: {self.role}")


# ─── SUBCLASS 1 ───────────────────────────────────────
class AdminUser(User):
    def __init__(self, username, permissions):
        super().__init__(username, role="admin")   # calls User.__init__
        self.permissions = permissions             # extra attribute

    def display_profile(self):                     # override ✅
        perms = ", ".join(self.permissions)        # list → string
        print(f"Admin: {self.username} | Permissions: {perms}")


# ─── SUBCLASS 2 ───────────────────────────────────────
class CustomerUser(User):
    def __init__(self, username, orders_count):
        super().__init__(username, role="customer") # calls User.__init__
        self.orders_count = orders_count            # extra attribute

    def display_profile(self):                      # override ✅
        print(f"Customer: {self.username} | Orders: {self.orders_count}")


# ─── RUNNING THE CODE ─────────────────────────────────
admin    = AdminUser("admin1", ["manage_users", "view_logs"])
customer = CustomerUser("cust1", 5)

admin.display_profile()
customer.display_profile()