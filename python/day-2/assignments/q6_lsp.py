#Q6. LSP — Fix the Bird Hierarchy 
from abc import ABC, abstractmethod

# ─── BASE CLASS ───────────────────────────────────────
# every bird can move — that's all we guarantee
class Bird(ABC):
    @abstractmethod
    def move(self):
        pass


# ─── FLYING BIRD ──────────────────────────────────────
# only birds that CAN fly inherit this
class FlyingBird(Bird):
    @abstractmethod
    def fly(self):
        pass

    def move(self):
        self.fly()                    # move = fly for flying birds


# ─── SWIMMING BIRD ────────────────────────────────────
# only birds that CAN swim inherit this
class SwimmingBird(Bird):
    @abstractmethod
    def swim(self):
        pass

    def move(self):
        self.swim()                   # move = swim for swimming birds


# ─── SPARROW ──────────────────────────────────────────
class Sparrow(FlyingBird):            # IS-A FlyingBird ✅
    def fly(self):
        print("Sparrow flies")


# ─── EAGLE ────────────────────────────────────────────
class Eagle(FlyingBird):              # IS-A FlyingBird ✅
    def fly(self):
        print("Eagle flies")


# ─── PENGUIN ──────────────────────────────────────────
class Penguin(SwimmingBird):          # IS-A SwimmingBird ✅
    def swim(self):
        print("Penguin swims")


# ─── DUCK ─────────────────────────────────────────────
# Duck BOTH flies and swims
class Duck(FlyingBird, SwimmingBird): # inherits BOTH ✅
    def fly(self):
        print("Duck flies")

    def swim(self):
        print("Duck swims")

    def move(self):                   # overrides both move()
        print("Duck flies and swims") # custom behavior


# ─── RUNNING THE CODE ─────────────────────────────────
for bird in [Sparrow(), Eagle(), Penguin(), Duck()]:
    bird.move()