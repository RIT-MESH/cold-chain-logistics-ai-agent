"""Import the single unified dataset into an empty SQL Server database."""
from pathlib import Path
import runpy
runpy.run_path(str(Path(__file__).resolve().parents[1] / "data" / "unified" / "import_sql_server.py"), run_name="__main__")
