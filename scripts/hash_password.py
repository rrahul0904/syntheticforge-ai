#!/usr/bin/env python3
"""Create a PBKDF2-SHA256 password hash for the production admin/operator config."""
from getpass import getpass

from app.security import hash_password


if __name__ == "__main__":
    first = getpass("New password (minimum 14 characters): ")
    second = getpass("Repeat password: ")
    if first != second:
        raise SystemExit("Passwords did not match")
    print(hash_password(first))
