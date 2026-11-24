"""Completion metadata, grouped without executing component handlers."""
from .data import CATEGORIES
from .registry import supported


def text(value):
    return ' '.join(str(value).split())


def component_groups(components, platform):
    groups = [('management', '框架管理'), *CATEGORIES.items()]
    for group, label in groups:
        choices = [('list', '列出全部组件')] if group == 'management' else []
        choices += [(name, text(spec['description'])) for name, spec in components.items()
                    if (('management' if spec.get('kind') == 'command' else spec['category']) == group
                        and platform in spec['platforms'])]
        for name, description in sorted(choices):
            yield f'{group}\t{label}\t{name}:{description}'


def action_descriptions(spec, platform):
    for action, info in sorted(spec['actions'].items()):
        if supported(spec, action, platform):
            yield action + ':' + text(info['description'])


def options(spec, action):
    for option, description in sorted(spec['actions'][action].get('options', {}).items()):
        yield option + ':' + text(description)
