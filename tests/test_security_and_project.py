import ast
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from python.database import init_database  # noqa: E402


class TestSecurityAndProjectDeliverables(unittest.TestCase):
    """Audit script permissions, subprocess safety, DB permissions, and required docs."""

    def test_bash_scripts_are_executable_and_not_world_writable(self) -> None:
        """Verify all bash/*.sh scripts have execute bits and forbid world-write (o+w)."""
        bash_dir = PROJECT_ROOT / "bash"
        scripts = list(bash_dir.glob("*.sh"))
        self.assertGreaterEqual(len(scripts), 5)

        for script in scripts:
            mode = script.stat().st_mode
            self.assertTrue(
                os.access(script, os.X_OK),
                f"Script {script.name} is not executable",
            )
            self.assertEqual(
                mode & stat.S_IWOTH,
                0,
                f"Security risk: {script.name} is world-writable (o+w)!",
            )

    def test_database_file_enforces_0600_owner_only_permissions(self) -> None:
        """Verify init_database restricts SQLite file permissions to 0600 (rw-------)."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "secure_metrics.db"
            init_database(db_path)
            permission_bits = stat.S_IMODE(db_path.stat().st_mode)
            self.assertEqual(
                permission_bits,
                0o600,
                f"Expected 0o600 on database file, got {oct(permission_bits)}",
            )

    def test_python_modules_never_use_subprocess_shell_true(self) -> None:
        """Parse AST of all python/*.py files to verify shell=True is never used."""
        python_dir = PROJECT_ROOT / "python"
        for py_file in python_dir.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for kw in node.keywords:
                        if kw.arg == "shell" and isinstance(kw.value, ast.Constant):
                            self.assertFalse(
                                bool(kw.value.value),
                                f"Security violation: shell=True found in {py_file.name}",
                            )

    def test_gitignore_excludes_sensitive_runtime_files(self) -> None:
        """Verify .gitignore excludes logs, SQLite databases, WAL files, and lockfiles."""
        gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        for required_pattern in ("logs/*.log", "data/*.db", "data/*.db-wal", "data/*.lock"):
            self.assertIn(required_pattern, gitignore)

    def test_required_documentation_and_config_files_exist(self) -> None:
        """Verify README.md, ARCHITECTURE.md, requirements.txt, and thresholds.json exist."""
        required_files = (
            "README.md",
            "ARCHITECTURE.md",
            "requirements.txt",
            "config/thresholds.json",
        )
        for rel_path in required_files:
            full_path = PROJECT_ROOT / rel_path
            self.assertTrue(full_path.is_file(), f"Missing deliverable file: {rel_path}")
            self.assertGreater(full_path.stat().st_size, 50)


if __name__ == "__main__":
    unittest.main()
