# Gemini CLI Project Rules for Algo Trading System

This file contains foundational instructions and guidelines for interacting with the `Algo-main` codebase. As an AI assistant, you must adhere strictly to these rules.

## 1. Project Context
This is an Algorithmic Trading system written in Python. It includes backtesting, live trading, broker integration (Upstox), risk management, technical indicators, and machine learning components. It features a Streamlit-based dashboard for monitoring and visualization.

## 2. Coding Standards
- **Python Conventions:** Follow PEP 8 guidelines. Code should be clean, readable, and pythonic.
- **Type Hinting:** Use static typing (`typing` module) comprehensively across all function arguments and return types to improve safety and autocompletion.
- **Docstrings:** Use Google-style docstrings for all modules, classes, and public functions. Briefly explain the parameters, return types, and any exceptions raised.
- **No Hardcoded Secrets:** Never hardcode credentials, API keys, or broker tokens. Always use the `python-dotenv` package and expect secrets to be loaded via environment variables from a `.env` file.

## 3. Architectural Guidelines
- **Modularity:** Respect the existing folder structure. Do not mix concerns.
  - `backtest/`: Engine and metrics for simulating trades.
  - `broker/`: Live broker API interactions (e.g., Upstox).
  - `config/`: System configurations (`.yaml` files and environment setups).
  - `dashboard/`: Streamlit UI and Plotly charts. Do not put heavy trading logic here.
  - `data/`: Market data fetching (Upstox, NSE instruments).
  - `db/`: SQLAlchemy database models and repository logic.
  - `indicators/`: Technical indicators calculation (often using `ta` or `pandas`/`numpy`).
  - `ml/`: Machine learning models (`scikit-learn` phase).
  - `portfolio/` & `risk/`: Allocation, sizing, and risk management limits.
  - `strategy/`: Entry/exit signals and core trading rules.
- **Error Handling:** Implement robust error handling, especially for network requests (Broker APIs, Data fetching) and database operations. Log errors using the established logging mechanism (`monitoring/logger.py`).

## 4. Testing & Validation
- **Framework:** Use `pytest` for all unit and integration tests.
- **Test Locations:** Ensure tests reside in the `tests/` directory and follow the `test_<module>.py` naming convention.
- **Mocking:** When testing broker integrations or external APIs, aggressively mock external calls (e.g., using `unittest.mock`) to prevent unintended live API requests or trades during testing.
- **Validation:** Always verify changes by running the relevant `pytest` suite before concluding a task.

## 5. Security & Safety First
- **Zero Real Trading without Confirmation:** If an instruction touches live execution modules (`broker/`, `runner/`), strictly ensure there's a paper trading fallback or prompt for verification if behavior could execute real trades.
- **Logging:** Avoid logging sensitive information such as broker access tokens or order secrets.

## 6. Execution Protocol
- **Research before modifying:** Use `grep_search`, `glob`, and `read_file` to understand how the components interplay before proposing a change.
- **Surgical Edits:** Keep code modifications scoped to exactly what the user requested. If adding a feature involves multiple components (e.g., a new indicator needing a dashboard update), systematically update them while keeping commits/changes logical.
