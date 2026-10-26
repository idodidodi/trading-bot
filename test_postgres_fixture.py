"""Fixture safety checks run without any database or provider credentials."""
import os
import unittest
from unittest.mock import patch
from test_postgres_support import PostgresTestCase


class FixtureTests(unittest.TestCase):
    def test_missing_database_skips_before_subclass_setup(self):
        calls=[]
        class DatabaseCase(PostgresTestCase):
            def setUp(self):calls.append('setup')
            def test_database(self):calls.append('test')
        with patch.dict(os.environ,{},clear=True),patch('test_postgres_support.reset') as reset:
            result=DatabaseCase('test_database').run()
        self.assertEqual(result.testsRun,1)
        self.assertEqual(len(result.skipped),1)
        self.assertEqual(result.errors,[])
        self.assertEqual(calls,[])
        reset.assert_not_called()

    def test_configured_database_resets_before_subclass_setup(self):
        calls=[]
        class DatabaseCase(PostgresTestCase):
            def setUp(self):calls.append('setup')
            def test_database(self):calls.append('test')
        with patch.dict(os.environ,{'TRADING_TEST_DATABASE_URL':'fixture-only'},clear=True),patch('test_postgres_support.reset',side_effect=lambda:calls.append('reset')):
            result=DatabaseCase('test_database').run()
        self.assertTrue(result.wasSuccessful())
        self.assertEqual(result.skipped,[])
        self.assertEqual(calls,['reset','setup','test'])
