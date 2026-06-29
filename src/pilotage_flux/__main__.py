"""Permet `python -m pilotage_flux ...` sans warning runpy."""

from pilotage_flux.cli.main import app

if __name__ == "__main__":
    app()
