"""One grammar for built-in and local components: COMPONENT ACTION [TARGET ...]."""
import argparse
import json
import os
from pathlib import Path
import sys
from . import __version__
from . import engine, registry
from .data import CATEGORIES, tool_specs
from .ui import Console, component_help, notation, ArgumentParser
from .platforms import detect, SUPPORTED

REPO = Path(__file__).resolve().parents[2]


def load_profile(repo, name, seen=()):
    if not registry.NAME.fullmatch(name) or name in seen:
        raise RuntimeError('Invalid or cyclic installation profile: ' + name)
    path = repo / 'profiles' / (name + '.toml')
    if not path.is_file(): raise RuntimeError('Unknown installation profile: ' + name)
    spec = registry.read(path)
    if spec.get('schema') != 1: raise RuntimeError('Unsupported profile schema: ' + name)
    result = dict(spec, install=[], check=[], after_install=[])
    for parent in spec.get('extends', []):
        inherited = load_profile(repo, parent, (*seen, name))
        if not set(spec['platforms']) <= set(inherited['platforms']):
            raise RuntimeError(name + ': parent does not support the selected platforms')
        for action in ('install', 'check', 'after_install'): result[action].extend(inherited[action])
    for action in ('install', 'check', 'after_install'): result[action].extend(spec.get(action, []))
    return result


def workflow(components, action, args, repo, root, platform):
    profile = args[0] if args and not args[0].startswith('-') else (platform if platform in ('steamdeck', 'macos') else 'workstation')
    rest = args[1:] if args and args[0] == profile else args
    if rest:
        raise RuntimeError('Unexpected system arguments: ' + ' '.join(rest))
    spec = load_profile(repo, profile)
    if platform not in spec['platforms']:
        raise RuntimeError(profile + ' is not supported on ' + platform)
    steps, installed = [], set()
    entries = spec['check'] if action == 'check' else spec['install'] + spec['check']
    if action == 'install':
        entries += spec['after_install']
    platform_path = repo / 'platforms' / platform / 'platform.toml'
    platform_spec = registry.read(platform_path) if platform_path.is_file() else {}
    if target := platform_spec.get('mirror_target'):
        entries = [dict(component='mirrors', action=action, args=[target])] + entries
    for item in entries:
        if not item.get('component') or 'module' in item:
            raise RuntimeError('Installation profile requires component fields: ' + profile)
        operation = item['action']
        if action == 'update' and operation == 'install':
            operation = 'update'
        planned = engine.plan(components, item['component'], operation, item.get('args', []), repo, root, platform)
        for step in planned:
            key = (step['component'], step['action'], tuple(step['command']))
            if key in installed:
                continue
            steps.append(step); installed.add(key)
    return steps


def main(argv=None):
    parser = ArgumentParser(prog='workbench', add_help=False, formatter_class=argparse.RawDescriptionHelpFormatter,
        usage='workbench [options] [list]\n       workbench [options] <component> <action> [<target> ...]',
        description='跨平台工作站管理。无参数或 list 列出组件；<参数> 必填，[参数] 可选，... 可重复。',
        epilog='示例：\n  workbench node list\n  workbench fd update --plan\n  workbench components create my-app')
    parser._positionals.title = '位置参数'
    parser._optionals.title = '选项'
    parser.add_argument('-h', '--help', action='help', help='显示帮助')
    parser.add_argument('--prefix', type=Path, metavar='<dir>', help='Software 安装目录（放在组件名前）', default=Path(os.environ.get('SOFTWARE_HOME', str(Path.home() / 'Software'))))
    parser.add_argument('--platform', choices=SUPPORTED, metavar='<platform>', help='预览平台：macos|steamdeck|fedora|ubuntu|debian；跨平台仅支持 --plan')
    parser.add_argument('--complete', choices=['components', 'component-groups', 'actions', 'action-descriptions',
                                              'options', 'targets', 'categories', 'tags', 'packages'], help=argparse.SUPPRESS)
    parser.add_argument('--category', choices=CATEGORIES, help='按用途分类筛选列表')
    parser.add_argument('--tag', help='按标签筛选列表')
    parser.add_argument('--version', action='version', version=__version__, help='显示版本')
    parser.add_argument('--json', action='store_true', help='组件列表或 --plan 使用 JSON 输出')
    parser.add_argument('--plan', action='store_true', help='仅预览，不执行或写入')
    parser.add_argument('component', nargs='?', help='组件名称；list 列出全部组件')
    parser.add_argument('args', nargs=argparse.REMAINDER, help='<action> [<target> ...] [options]')
    args = parser.parse_args(argv)
    console = Console()
    try:
        root = args.prefix.expanduser().resolve()
        host = detect()
        platform = args.platform or host
        components = registry.discover(REPO, root)
        rest = args.args
        if args.component in (None, 'list') and not args.complete:
            filters = ArgumentParser(prog='workbench list', add_help=False)
            filters.add_argument('--category', choices=CATEGORIES, default=args.category)
            filters.add_argument('--tag', default=args.tag)
            selected, rest = filters.parse_known_args(rest)
            args.category, args.tag = selected.category, selected.tag
        elif (args.category or args.tag) and not args.complete:
            raise RuntimeError('--category/--tag filter list output only')
        plan_only = args.plan or '--plan' in rest
        as_json = args.json or '--json' in rest
        rest = [a for a in rest if a not in ('--plan', '--json')]
        if args.complete:
            from . import completion
            if args.complete == 'categories': print('\n'.join(CATEGORIES)); return 0
            if args.complete == 'tags': print('\n'.join(sorted({tag for spec in components.values() for tag in spec.get('tags', [])}))); return 0
            if args.complete == 'packages': print('\n'.join(tool_specs(REPO))); return 0
            if args.complete == 'components':
                print('\n'.join(name + ':' + spec['description'] for name, spec in components.items()))
            elif args.complete == 'component-groups':
                print('\n'.join(completion.component_groups(components, platform)))
            elif args.component in components:
                spec = components[args.component]
                if args.complete == 'actions':
                    print('\n'.join(spec['actions']))
                elif args.complete == 'action-descriptions':
                    print('\n'.join(completion.action_descriptions(spec, platform)))
                elif rest and rest[0] in spec['actions']:
                    if args.complete == 'options':
                        print('\n'.join(completion.options(spec, rest[0])))
                    elif args.complete == 'targets':
                        print('\n'.join(registry.targets(spec, rest[0], REPO)))
            return 0
        if args.component in (None, 'list'):
            if rest: raise RuntimeError('Unexpected list arguments: ' + ' '.join(rest))
            data = [dict(name=name, description=spec['description'], platforms=spec['platforms'],
                         category=spec.get('category'), tags=spec.get('tags', []), kind=spec.get('kind', 'software'),
                         actions=list(spec['actions']), available=platform in spec['platforms']) for name, spec in components.items()
                    if (not args.category or spec.get('category') == args.category)
                    and (not args.tag or args.tag in spec.get('tags', []))]
            if as_json:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                console.title('workbench ' + __version__ + '  /  ' + platform)
                console.print('用法：workbench <component> <action> [<target> ...] [options]')
                console.print()
                for category, title in [*CATEGORIES.items(), (None, '框架管理')]:
                    members = [item for item in data if item['category'] == category]
                    if not members: continue
                    console.title(title + ('  [' + category + ']' if category else ''))
                    console.table([(item['name'], item['description'] +
                                    ('' if item['available'] else ' [当前平台不可用]')) for item in members])
                    console.print()
                if not data: console.print('没有匹配的组件。')
                console.print()
                notation(console)
                console.print('帮助：workbench <component> --help；详细参数：workbench <component> <action> --help')
            return 0
        if args.component not in components:
            raise RuntimeError('Unknown component: ' + args.component + '; run workbench list')
        spec = components[args.component]
        if not rest or rest[0] in ('help', '-h', '--help'):
            component_help(console, args.component, spec)
            return 0
        action, tail = rest[0], rest[1:]
        if action not in spec['actions']:
            raise RuntimeError('Unsupported action: ' + action + '; choose ' + ', '.join(spec['actions']))
        if any(a in ('-h', '--help') for a in tail):
            component_help(console, args.component, spec, action, registry.targets(spec, action, REPO))
            return 0
        choices = registry.targets(spec, action, REPO)
        positional = tail[:next((i for i, arg in enumerate(tail) if arg.startswith('-')), len(tail))]
        if positional and spec['actions'][action].get('arguments', spec.get('arguments')) == '' and not choices:
            raise RuntimeError(args.component + ' ' + action + ' takes no targets')
        if choices and any(value not in choices for value in positional):
            raise RuntimeError('Unknown target; choose: ' + ' '.join(choices))
        if args.component == 'system' and action in ('install', 'update', 'check'):
            steps = workflow(components, action, tail, REPO, root, platform)
        else:
            steps = engine.plan(components, args.component, action, tail, REPO, root, platform)
        if plan_only:
            if as_json:
                print(json.dumps(dict(platform=platform, prefix=str(root), steps=steps), ensure_ascii=False, indent=2))
            else:
                import shlex
                console.status('PLAN', str(len(steps)) + ' 个步骤；未执行', 'warn')
                rows = []
                for index, step in enumerate(steps, 1):
                    rows.extend([(str(index) + '. ' + step['component'], step['action']),
                                 ('', shlex.join(step['command']))])
                console.table(rows)
            return 0
        if platform != host:
            raise RuntimeError('--platform may only target another OS with --plan; actual host is ' + host)
        return engine.execute(steps, REPO, root, platform)
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        Console(sys.stderr).status('ERROR', str(error), 'error')
        return 1
