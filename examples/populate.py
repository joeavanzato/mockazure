"""Use the installed MockAzure population CLI; no separate scenario implementation."""
from mockazure.commands.populate import app

if __name__ == "__main__":
    app()
