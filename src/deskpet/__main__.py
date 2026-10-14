"""支持 ``python -m deskpet``（与 ``python run.py`` / ``python main.py`` 行为一致）。"""


import sys

from .app import run

if __name__ == "__main__":
    sys.exit(run())
