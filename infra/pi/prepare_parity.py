#!/usr/bin/env python3
"""Materialize allowlisted common policy into native candidates; never activate.

Reads only the two selected native JSON surfaces. No credentials, trust or session
stores are read. Outputs must be vacant and outside the selected agent directory.
"""
import argparse
import copy
import json
import re
from pathlib import Path

BASELINE = Path(__file__).with_name('common-baseline.json')
SETTINGS_KEYS = {'defaultProvider', 'defaultModel', 'defaultThinkingLevel',
                 'enableInstallTelemetry', 'cacheWarming'}
BINDINGS = {'home', 'agentDir', 'routeRoot', 'containerRoot', 'skillsRoot', 'fastRoot'}
BUILTINS = ['mcp', 'llama.cpp', 'codemode', 'tool-search']


def object_file(path):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


def strings(value):
    if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
        raise ValueError('Expected a list of nonempty strings')
    return value


def shape(value, expected):
    """Reject malformed or extra nested keys before materialization has effects."""
    if isinstance(expected, dict):
        if not isinstance(value, dict) or set(value) != set(expected):
            raise ValueError('Unknown or missing nested baseline fields')
        for key, child in expected.items():
            shape(value[key], child)
    elif expected is list:
        strings(value)
    elif type(value) is not expected:
        raise ValueError('Invalid baseline value type')


def validate_baseline(value):
    if set(value) != {'version', 'upstreamCommit', 'settings', 'extensions', 'skills', 'subagents', 'config'}:
        raise ValueError('Unknown or missing baseline fields')
    if value['version'] != 1 or not re.fullmatch('[0-9a-f]{40}', value['upstreamCommit']):
        raise ValueError('Unsupported baseline version or pin')
    sub = value['subagents']
    strings(value['extensions'])
    strings(value['skills'])
    shape(value['settings'], {key: bool if key == 'enableInstallTelemetry' else str for key in SETTINGS_KEYS})
    shape(sub, {'disableBuiltins': bool, 'defaultExtensions': list, 'agentExcludeDirs': list,
                'modelScope': {'enforce': bool, 'strict': bool, 'allow': list}})
    config = value['config']
    shape(config, {
        'autoResume': bool, 'asyncByDefault': bool, 'forceTopLevelAsync': bool,
        'defaultSubagentContext': str, 'maxSubagentDepth': int, 'artifactDir': str,
        'defaultSessionDir': str, 'singleRunOutputBaseDir': str,
        'scheduledRuns': {'enabled': bool, 'storeRoot': str},
        'missions': {'enabled': bool, 'globalIndex': bool},
        'disabledFeatures': list, 'orcaProgressTabs': {'enabled': bool},
    })
    if (config['autoResume'] is not False or config['forceTopLevelAsync'] is not True
            or config['scheduledRuns']['enabled'] is not False
            or config['defaultSubagentContext'] != 'fresh' or sub['disableBuiltins'] is not True):
        raise ValueError('Unsafe automatic execution or discovery policy')
    return value


def expand(value, bindings):
    if isinstance(value, str):
        for key, replacement in bindings.items():
            value = value.replace('{' + key + '}', replacement)
        if '{' in value or '}' in value:
            raise ValueError('Unknown baseline placeholder')
        return value
    if isinstance(value, list):
        return [expand(item, bindings) for item in value]
    if isinstance(value, dict):
        return {key: expand(item, bindings) for key, item in value.items()}
    return value


def merge_owned(current, desired):
    result = copy.deepcopy(current)
    for key, value in desired.items():
        if key == 'disabledFeatures':
            result[key] = list(dict.fromkeys(strings(result.get(key, [])) + strings(value)))
        elif isinstance(value, dict):
            prior = result.get(key, {})
            if not isinstance(prior, dict):
                raise ValueError('Native object field has incompatible type: ' + key)
            result[key] = merge_owned(prior, value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def append_resources(current, additions):
    result = list(strings(current))
    for item in additions:
        if '-' + item in result or '!' + item in result:
            raise ValueError('Required resource is natively disabled: ' + item)
        if item not in result:
            result.append(item)
    return result


def materialize(baseline, settings, config, bindings, old_package=None):
    validate_baseline(baseline)
    if set(bindings) != BINDINGS:
        raise ValueError('Bindings must specify exactly the six nonsecret local roots')
    for root in bindings.values():
        if not isinstance(root, str) or not Path(root).is_absolute() or '{' in root or '}' in root:
            raise ValueError('Bindings must be absolute local paths')
    policy = expand(baseline, bindings)
    result = merge_owned(settings, policy['settings'])
    extensions = strings(result.get('extensions', []))
    extensions = [p for p in extensions if p != '/opt/pi-subagent-local/index.ts']
    # Explicit MCP enrollment survives preparation; default remains disabled.
    # No other builtin may opt out of the common disables through this path.
    builtins = [b for b in BUILTINS if b != 'mcp' or 'builtin:mcp' not in extensions]
    result['extensions'] = append_resources(extensions, ['-builtin:' + b for b in builtins] + policy['extensions'])
    packages = result.get('packages', [])
    if not isinstance(packages, list):
        raise ValueError('Native packages must be an array')
    # Remove only the explicitly selected legacy package; retain native disables.
    result['packages'] = [p for p in packages if (p.get('source') if isinstance(p, dict) else p) != old_package]
    result['skills'] = append_resources(result.get('skills', []),
                                        [str(Path(bindings['skillsRoot']) / name) for name in policy['skills']])
    native_sub = result.get('subagents', {})
    exclusions = append_resources(native_sub.get('agentExcludeDirs', []), policy['subagents']['agentExcludeDirs'])
    result['subagents'] = merge_owned(native_sub, policy['subagents'])
    result['subagents']['agentExcludeDirs'] = exclusions
    return result, merge_owned(config, policy['config'])


def prepare(args):
    bindings = object_file(args.bindings)
    before = object_file(args.settings)
    config = object_file(args.config) if args.config else {}
    settings, native = materialize(object_file(args.baseline), before, config, bindings, args.old_package)
    expected = {'settings.json': settings, 'subagent-config.json': native}
    output = Path(args.output).resolve()
    discovery = [Path(bindings[key]).resolve() for key in ('agentDir', 'routeRoot', 'skillsRoot', 'containerRoot', 'fastRoot')]
    native_settings = Path(args.settings).resolve()
    if native_settings.name == 'settings.json':
        discovery.append(native_settings.parent)
    if any(output == root or root in output.parents for root in discovery) or {'.pi', '.agents'} & set(output.parts):
        raise ValueError('Candidates must be outside live discovery')
    if args.check:
        if any(object_file(output / name) != value for name, value in expected.items()):
            raise ValueError('Candidate drift detected')
        print('Candidates match common baseline and selected native inputs')
        return
    if output.exists():
        raise ValueError('Refusing occupied candidate output')
    # All parsing, validation and native comparisons finish before first write.
    output.mkdir(parents=True, mode=0o700)
    for name, value in expected.items():
        path = output / name
        path.write_text(json.dumps(value, indent=2) + '\n')
        path.chmod(0o600)
    print('Prepared native candidates only; no activation')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', default=str(BASELINE))
    parser.add_argument('--settings', required=True)
    parser.add_argument('--config')
    parser.add_argument('--bindings', required=True)
    parser.add_argument('--old-package')
    parser.add_argument('--output', required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    try:
        prepare(args)
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        parser.exit(1, f'Parity preparation refused: {exc}\n')


if __name__ == '__main__':
    main()
