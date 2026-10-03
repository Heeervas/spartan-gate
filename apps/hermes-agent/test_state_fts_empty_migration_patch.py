import importlib.util
from pathlib import Path
import sqlite3
import unittest

spec = importlib.util.spec_from_file_location('empty_fts_patch', Path(__file__).parent / 'patches/patch_state_fts_empty_migration.py')
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)

SOURCE = '''class Migration:
    def _migrate_misaligned_fts_source(self, cursor):
        has_messages = cursor.execute("SELECT 1 FROM messages LIMIT 1").fetchone() is not None
        def do_align():
            cursor.execute("DROP TABLE messages_fts")
            self._ensure_fts_schema(cursor, "messages_fts", FTS_SQL)
            if has_messages:
                cursor.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
            cursor.execute("INSERT INTO state_meta VALUES ('fts_storage_version', '3')")
        if not has_messages:
            cursor.execute("SAVEPOINT fts_align_empty")
            try:
                do_align()
                cursor.execute("RELEASE SAVEPOINT fts_align_empty")
            except BaseException:
                cursor.execute("ROLLBACK TO SAVEPOINT fts_align_empty")
                cursor.execute("RELEASE SAVEPOINT fts_align_empty")
                raise
            return
        self._run_admitted_startup_rebuild(cursor, do_align)
'''
DDL = '''CREATE VIEW messages_fts_src AS SELECT id, content FROM messages;
CREATE VIRTUAL TABLE messages_fts USING fts5(content, content='messages_fts_src', content_rowid='id');
'''


class EmptyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        self.conn.executescript('''CREATE TABLE messages(id INTEGER PRIMARY KEY, content TEXT);
CREATE TABLE state_meta(key TEXT PRIMARY KEY, value TEXT);
CREATE VIRTUAL TABLE messages_fts USING fts5(content, content='messages', content_rowid='id');''')

    def migration(self, fixed=True, fail=False):
        ns = {'FTS_SQL': DDL}
        exec(patcher.patch_text(SOURCE) if fixed else SOURCE, ns)
        migration = ns['Migration']()
        migration._ensure_fts_schema = lambda cursor, name, ddl: cursor.executescript(ddl)
        migration._run_admitted_startup_rebuild = lambda cursor, callback: callback()
        def transactional(cursor, ddl):
            statement = ''
            for line in ddl.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    cursor.execute(statement)
                    statement = ''
            if fail:
                raise RuntimeError('injected failure after DDL')
        migration._execute_ddl_script_transactional = transactional
        return migration

    def test_original_reproduces_lost_savepoint(self):
        with self.assertRaisesRegex(sqlite3.OperationalError, 'no such savepoint'):
            self.migration(fixed=False)._migrate_misaligned_fts_source(self.conn.cursor())

    def test_empty_migration_commits_aligned_index(self):
        self.migration()._migrate_misaligned_fts_source(self.conn.cursor())
        self.assertEqual(self.conn.execute('SELECT value FROM state_meta').fetchone(), ('3',))
        self.assertFalse(self.conn.in_transaction)

    def test_failure_rolls_back_index_and_completion_marker(self):
        with self.assertRaisesRegex(RuntimeError, 'injected failure'):
            self.migration(fail=True)._migrate_misaligned_fts_source(self.conn.cursor())
        sql = self.conn.execute("SELECT sql FROM sqlite_master WHERE name='messages_fts'").fetchone()[0]
        self.assertIn("content='messages'", sql)
        self.assertEqual(self.conn.execute('SELECT * FROM state_meta').fetchall(), [])
        self.assertFalse(self.conn.in_transaction)

    def test_populated_path_keeps_native_rebuild(self):
        self.conn.execute("INSERT INTO messages VALUES (1, 'searchable')")
        self.conn.commit()
        self.migration()._migrate_misaligned_fts_source(self.conn.cursor())
        self.assertEqual(self.conn.execute("SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'searchable'").fetchall(), [(1,)])

    def test_patch_is_idempotent_and_rejects_drift(self):
        fixed = patcher.patch_text(SOURCE)
        self.assertEqual(patcher.patch_text(fixed), fixed)
        with self.assertRaisesRegex(RuntimeError, 'anchor drift'):
            patcher.patch_text(SOURCE.replace('self._ensure_fts_schema', 'self.changed_helper'))
