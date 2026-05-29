class Greeter:
    def __init__(self, name: str, greeting: str = "Hello") -> None:
        self.name = name
        self.greeting = greeting

    def greet(self) -> str:
        return f"{self.greeting}, {self.name}!"

    def shout(self) -> str:
        return self.greet().upper()


if __name__ == "__main__":
    g = Greeter("world")
    print(g.greet())
    print(g.shout())
    print(Greeter("Bhavy", greeting="Hi").greet())
