"""Ordered subscription rules with a protected, platform-owned prefix."""
import copy
import hashlib

BUILTINS = {'DIRECT', 'REJECT', 'REJECT-DROP', 'PASS', 'COMPATIBLE', 'GLOBAL'}


def mapped_reference(name, mapping, index):
    if name in mapping:
        return mapping[name]
    if name in BUILTINS:
        return name
    raise RuntimeError(f'Subscription {index}: unresolved policy reference; old configuration retained')


def split_rule(rule):
    """Split the final policy without splitting commas in logical expressions."""
    if not isinstance(rule, str):
        raise RuntimeError('Routing rules must be strings')
    parts = [part.strip() for part in rule.split(',')]
    flags = []
    while parts and parts[-1] in ('no-resolve', 'src'):
        flags.insert(0, parts.pop())
    if len(parts) < 2:
        raise RuntimeError('Routing rule is missing a policy')
    target = parts.pop()
    return ','.join(parts), target, flags


def rule_text(condition, target, flags):
    return ','.join([condition, target, *flags])


def merge(config, records, mappings, folder):
    """Keep the platform prefix, source order, then only the primary MATCH.

    Secondary groups/nodes are already namespaced by the subscription importer.
    External rule providers retain native updates; their cache paths are isolated.
    """
    primary = records[0]['document']['rules']
    if config['rules'][-len(primary):] != primary:
        raise RuntimeError('Cannot identify the subscription rule boundary safely')
    prefix = config['rules'][:-len(primary)]
    sections = [('global-override', prefix)]
    fallback = []
    providers = config.setdefault('rule-providers', {})
    for index, (record, mapping) in enumerate(zip(records, mappings), 1):
        doc = record['document']
        rules = doc.get('rules', [])
        if not isinstance(rules, list):
            raise RuntimeError(f'Subscription {index}: rules must be a list')
        identity = hashlib.sha256((record['url'] or 'local-primary').encode()).hexdigest()[:12]
        provider_names = {}
        if index > 1:
            if doc.get('sub-rules'):
                raise RuntimeError(f'Subscription {index}: sub-rules require an explicit merge; old configuration retained')
            for name, source in doc.get('rule-providers', {}).items():
                new_name = f'subscription-{identity}-{name}'
                if new_name in providers:
                    raise RuntimeError('Rule provider names collide after source prefixing')
                provider = copy.deepcopy(source)
                if provider.get('type') == 'file':
                    raise RuntimeError(f'Subscription {index}: remote YAML references a local rule file; use an inline or HTTP provider')
                if provider.get('type') == 'http':
                    digest = hashlib.sha256((name + str(provider.get('url', ''))).encode()).hexdigest()
                    provider['path'] = str(folder/'rule-cache'/identity/digest)
                if provider.get('proxy'):
                    provider['proxy'] = mapped_reference(provider['proxy'], mapping, index)
                providers[new_name] = provider
                provider_names[name] = new_name
        imported = []
        for position, rule in enumerate(rules):
            condition, target, flags = split_rule(rule)
            kind = condition.split(',', 1)[0]
            if kind == 'MATCH':
                if position != len(rules)-1 or flags:
                    raise RuntimeError(f'Subscription {index}: MATCH must be the final rule without options')
                if index == 1:
                    fallback.append(rule)
                continue
            if index > 1:
                if kind == 'SUB-RULE' or 'RULE-SET,' in condition and kind != 'RULE-SET':
                    raise RuntimeError(f'Subscription {index}: nested rule references require an explicit merge')
                target = mapped_reference(target, mapping, index)
                if kind == 'RULE-SET':
                    old_name = condition.split(',', 1)[1]
                    if old_name not in provider_names:
                        raise RuntimeError(f'Subscription {index}: rule provider reference is missing')
                    condition = 'RULE-SET,' + provider_names[old_name]
            imported.append(rule_text(condition, target, flags))
        sections.append((f'subscription-{identity}', imported))
    if not fallback:
        raise RuntimeError('First subscription must end with MATCH to define the shared default route')
    sections.append(('default', fallback))
    config['rules'] = [rule for _, rules in sections for rule in rules]
    return sections


def compile_providers(config, sections):
    """Only merge adjacent rules with the same action; never reorder matches.

    An inline provider is part of the same validated configuration transaction.
    A single subscription can need multiple providers because a RULE-SET has one
    action, whereas subscription rules can select many different policy groups.
    """
    config['x-workbench-expanded-rules'] = list(config['rules'])
    providers = config.setdefault('rule-providers', {})
    compiled = []
    for label, rules in sections:
        previous = None
        for number, rule in enumerate(rules, 1):
            condition, target, flags = split_rule(rule)
            kind = condition.split(',', 1)[0]
            # Keep native references and catch-all outside classical providers.
            if kind in ('MATCH', 'RULE-SET', 'SUB-RULE') or 'RULE-SET,' in condition:
                compiled.append(rule)
                previous = None
                continue
            signature = (target, tuple(flags))
            if previous is None or previous[0] != signature:
                name = f'workbench-{label}-{number:03d}'
                if name in providers:
                    raise RuntimeError('Subscription rule provider conflicts with a Workbench provider name')
                providers[name] = {'type': 'inline', 'behavior': 'classical', 'payload': []}
                compiled.append('RULE-SET,' + name + ',' + target)
                previous = signature, name
            providers[previous[1]]['payload'].append(','.join([condition, *flags]))
    config['rules'] = compiled


def expanded(config):
    """Validate original rule syntax too: classical providers may skip bad rows."""
    result = copy.deepcopy(config)
    result['rules'] = result.pop('x-workbench-expanded-rules', result['rules'])
    return result
