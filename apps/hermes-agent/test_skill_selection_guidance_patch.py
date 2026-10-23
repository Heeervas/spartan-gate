"""Native-source renderer regression; set HERMES_PROMPT_SOURCE for acceptance."""
import ast
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

PATCH = Path(__file__).parent / 'patches/patch_skill_selection_guidance.py'
spec = importlib.util.spec_from_file_location('skill_selection_patch', PATCH)
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)
SOURCE = os.environ.get('HERMES_PROMPT_SOURCE')
if not SOURCE and os.environ.get('HERMES_UPSTREAM_SOURCE'):
    SOURCE = str(Path(os.environ['HERMES_UPSTREAM_SOURCE']) / 'agent/prompt_builder.py')

@unittest.skipUnless(SOURCE, 'HERMES_PROMPT_SOURCE native input required')
class SkillSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(SOURCE).read_text()
        cls.updated = patcher.patch_text(cls.source)

    def renderer(self, source, config, oneshot, description="description"):
        tree = ast.parse(source)
        names = {'_render_skills_index', '_spartan_required_skill_selection'}
        body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        ns = {'_config_readonly': lambda key: config}
        code = 'from __future__ import annotations\n' + ast.unparse(ast.Module(body=body, type_ignores=[]))
        exec(compile(code, '<native renderer>', 'exec'), ns)
        one = types.ModuleType('agent.oneshot_footprint')
        one.is_single_query_session = lambda: oneshot
        one.ONESHOT_SKILLS_LOAD_GUIDANCE = '## Skills\nNative one-shot guidance\n'
        with patch.dict(sys.modules, {'agent': types.ModuleType('agent'), 'agent.oneshot_footprint': one}):
            return ns['_render_skills_index']({'coding':[('example',description)]}, {}, frozenset({'coding'}) if description == 'description' else None, {'terminal'})

    def test_config_opt_in_and_noop_consumers(self):
        for oneshot in [False, True]:
            original = self.renderer(self.source, {}, oneshot)
            for config in [{}, {'skills':None}, {'skills':[]}, {'skills':{'selection_policy':'unknown'}}]:
                self.assertEqual(self.renderer(self.updated, config, oneshot), original)
            changed = self.renderer(self.updated, {'skills':{'selection_policy':'required'}}, oneshot)
            self.assertIn('Include every mandatory method', changed)
            self.assertNotIn('even partially relevant', changed)
            self.assertNotIn('Native one-shot guidance', changed)
            for marker in ['coding [names only]: example', 'Categories marked [names only]']:
                self.assertIn(marker,changed)
                self.assertIn(marker,original)

    def test_catalog_and_quoted_content_preserved(self):
        for oneshot in [False, True]:
            original=self.renderer(self.source,{},oneshot)
            changed=self.renderer(self.updated,{'skills':{'selection_policy':'required'}},oneshot)
            catalog=lambda text:text[text.index('<available_skills>'):text.index('</available_skills>')+len('</available_skills>')]
            self.assertEqual(catalog(original),catalog(changed))
            # Quoted native prose supplied as a skill description remains data.
            quoted_original=self.renderer(self.source,{},oneshot,original)
            quoted_changed=self.renderer(self.updated,{'skills':{'selection_policy':'required'}},oneshot,original)
            self.assertEqual(catalog(quoted_original),catalog(quoted_changed))
            self.assertIn(original,quoted_changed)

    def test_idempotent_and_rejects_drift(self):
        self.assertEqual(patcher.patch_text(self.updated),self.updated)
        with self.assertRaisesRegex(ValueError,'anchors changed'):
            patcher.patch_text(self.source.replace(patcher.CACHE,'        changed_cache_key(),\n'))
        with self.assertRaisesRegex(ValueError,'incomplete'):
            patcher.patch_text(self.updated.replace(patcher.CACHE_REPLACEMENT,patcher.CACHE))

if __name__=='__main__':unittest.main()
