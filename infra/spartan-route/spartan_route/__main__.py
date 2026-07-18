import os

from .app import main


if __name__ == "__main__":
    os.umask(0o077)
    main()
