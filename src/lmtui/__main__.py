# ------ Entry point for `lmtui` script and `python -m lmtui` ------
from lmtui.app import LmTuiApp


def main() -> None:
    """Launch the Lossless Music TUI."""
    LmTuiApp().run()


if __name__ == "__main__":
    main()
