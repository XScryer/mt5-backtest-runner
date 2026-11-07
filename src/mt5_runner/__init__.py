"""mt5-runner — command-line batch runner for the MetaTrader 5 Strategy Tester.

Reads MT5 tester ``.ini`` configs, stages the matching ``.set`` parameter file
into the terminal's ``MQL5\\Profiles\\Tester`` directory, launches
``terminal64.exe /config:...`` with no GUI, waits for the run to finish, parses
the tester log (UTF-16, multi-GB safe) and appends one row per run to a summary
CSV.
"""

__version__ = "0.1.0"
