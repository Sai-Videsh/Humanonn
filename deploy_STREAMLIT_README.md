# Humanonn Streamlit UI

This lightweight Streamlit UI runs `humanonn` scans on the server so users can trigger scans from a browser without installing the CLI.

## Requirements (server)
- Python 3.11
- Install project deps: `pip install -e .`
- Install Playwright browsers: `python -m playwright install chromium`
- Streamlit: `pip install streamlit`

## Run

```bash
# from repo root
python -m venv .venv
# activate .venv\Scripts\activate (Windows) or source .venv/bin/activate (Linux/macOS)
pip install -e .
pip install streamlit
python -m playwright install chromium
streamlit run streamlit_app.py --server.port 8501
```

## Notes
- The Streamlit process will execute `python -m humanonn scan <url> --json <reports/...>` on the server host. Ensure the server has sufficient resources and Playwright is installed.
- For production, containerize the app and run in an environment with enough CPU/memory. See `Dockerfile` example in the repo for guidance.
