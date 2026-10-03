"""Preserve SQLite's savepoint when Hermes migrates an empty legacy FTS index."""
import ast
from pathlib import Path

TARGET = Path('/opt/hermes/hermes_state_schema.py')
ANCHOR = '            self._ensure_fts_schema(cursor, "messages_fts", FTS_SQL)'
REPLACEMENT = '''            # Spartan: executescript would implicitly commit the empty-index savepoint.
            if has_messages:
                self._ensure_fts_schema(cursor, "messages_fts", FTS_SQL)
            else:
                self._execute_ddl_script_transactional(cursor, FTS_SQL)'''


def patch_text(source):
    nodes = [node for node in ast.walk(ast.parse(source))
             if isinstance(node, ast.FunctionDef) and node.name == '_migrate_misaligned_fts_source']
    if len(nodes) != 1:
        raise RuntimeError('Missing or ambiguous empty FTS migration')
    node = nodes[0]
    lines = source.splitlines(keepends=True)
    body = ''.join(lines[node.lineno - 1:node.end_lineno])
    if REPLACEMENT in body:
        return source
    if body.count(ANCHOR) != 1 or 'SAVEPOINT fts_align_empty' not in body:
        raise RuntimeError('Empty FTS migration anchor drift')
    body = body.replace(ANCHOR, REPLACEMENT, 1)
    result = ''.join(lines[:node.lineno - 1]) + body + ''.join(lines[node.end_lineno:])
    compile(result, str(TARGET), 'exec')
    return result


def main():
    source = TARGET.read_text()
    updated = patch_text(source)
    if updated != source:
        TARGET.write_text(updated)
    print('OK: empty FTS migration keeps its savepoint')


if __name__ == '__main__':
    main()
