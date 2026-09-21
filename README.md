# lmtui

A custom Apple Music TUI for macOS. Controls native Music.app over
AppleScript so playback stays lossless. Built with Python + Textual.

## Status

Early scaffold.

## Setup

    python3.12 -m venv .venv
    source .venv/bin/activate
    pip install -e .
    lmtui

## Design notes

- **Why not the Apple Music API?** Requires a $99/yr dev account. AppleScript
  controls local Music.app with zero cost and full lossless playback.
- **Why half-block album art instead of Kitty graphics?** Ghostty's Kitty
  graphics implementation currently breaks image replacement
  (ghostty#6711). Unicode half-blocks render identically everywhere.
