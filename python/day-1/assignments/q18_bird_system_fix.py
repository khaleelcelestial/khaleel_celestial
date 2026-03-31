#Bird System Fix (LSP + Inheritance Design)
# Base class
class Bird:
    pass
# Flying behavior class
class FlyingBird(Bird):
    def fly(self):
        print("Flying...")
# Specific Birds
class Sparrow(FlyingBird):
    def fly(self):
        print("Sparrow flies")
class Penguin(Bird):
    def swim(self):
        print("Penguin swims")
sparrow = Sparrow()
sparrow.fly()

penguin = Penguin()
penguin.swim()