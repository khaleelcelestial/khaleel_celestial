from utils import register_user, login_user, view_users, delete_user

def main():
    while True:
        print("\n--- User Management System ---")
        print("1. Register User")
        print("2. Login")
        print("3. View Users")
        print("4. Delete User")
        print("5. Exit")

        choice = input("Enter choice: ")

        if choice == "1":
            register_user()

        elif choice == "2":
            login_user()

        elif choice == "3":
            view_users()

        elif choice == "4":
            delete_user()

        elif choice == "5":
            print("Exiting...")
            break

        else:
            print("Invalid choice")

if __name__ == "__main__":
    main()