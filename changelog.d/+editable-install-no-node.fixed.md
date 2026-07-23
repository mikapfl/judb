Installing judb for development (`uv sync`, `pip install -e .`) no longer
requires Node/pnpm. Building a real wheel or sdist still does, so released
artifacts always ship the frontend bundle.
