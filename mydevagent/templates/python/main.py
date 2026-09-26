"""Your Python program. Run it with: python main.py"""


def greet(name: str) -> str:
    return f"Hello, {name}!"


def main() -> None:
    name = input("What's your name? ").strip() or "friend"
    print(greet(name))


if __name__ == "__main__":
    main()
