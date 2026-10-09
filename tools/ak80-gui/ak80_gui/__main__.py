"""Запуск: python -m ak80_gui [--port /dev/ttyACM0]"""

import argparse
import os
import sys

from PySide6.QtWidgets import QApplication

from .snap_env import restore_host_env
from .window import MainWindow


def main() -> None:
    parser = argparse.ArgumentParser(description="Пульт стенда AK80-64")
    parser.add_argument(
        "--port", help="последовательный порт, например /dev/ttyACM0; с ним пульт подключается сразу")
    args, qt_args = parser.parse_known_args()

    # До создания QApplication: тема Qt подгружает GTK3, а та читает окружение.
    restore_host_env(os.environ)
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("AK80-64 пульт")
    window = MainWindow(port=args.port)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
