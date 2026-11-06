"""Architectural guard: the signal entrypoint must not launch a trading runtime."""
import ast
from pathlib import Path
import unittest


class SignalRuntimeBoundaryTests(unittest.TestCase):
    def test_signal_entrypoint_has_no_trading_worker_dependencies(self):
        tree=ast.parse(Path('platform_app.py').read_text())
        main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        imports={n.module for n in ast.walk(main) if isinstance(n,ast.ImportFrom)}
        self.assertFalse(imports & {'paper_trading','demo_execution','chasing_runtime','execution_gateway'})
        self.assertTrue({'scanner','telegram_checklist','colmex_runtime','backtest_jobs'}<=imports)
        # A removed worker must not remain in shutdown code (which would fail on restart).
        names={n.id for n in ast.walk(main) if isinstance(n,ast.Name)}
        self.assertFalse(names & {'paper_thread','chasing_thread','paper_worker','chasing_worker'})
        self.assertIn('delivery_worker',names)
