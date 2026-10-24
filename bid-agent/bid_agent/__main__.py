import argparse
import json
from pathlib import Path
from .store import Store
from .tools import ProjectTools
from .provider import Provider
from .core import Agent
from .skills import mount_skills


def main():
    parser = argparse.ArgumentParser(description='Deta bid Agent')
    parser.add_argument('--state', type=Path, default=Path('runs/state.sqlite'))
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create')
    create.add_argument('--project', type=Path, required=True)
    create.add_argument('--task', required=True)
    create.add_argument('--skill', action='append', default=[], help='name=/absolute/path')
    run = sub.add_parser('run')
    run.add_argument('id')
    run.add_argument('--steps', type=int, default=20)
    for name in ('status', 'cancel', 'recover'):
        sub.add_parser(name).add_argument('id')
    answer = sub.add_parser('answer')
    answer.add_argument('id')
    answer.add_argument('text')
    args = parser.parse_args()
    store = Store(args.state)
    if args.command == 'create':
        root = args.project.resolve(strict=True)
        skills, pins = mount_skills(dict(s.split('=', 1) for s in args.skill))
        print(store.create(args.task, {'project': str(root), 'skills': skills, 'skill_pins': pins}))
        return
    if args.command == 'answer':
        store.answer(args.id, args.text)
    elif args.command == 'cancel':
        store.transition(args.id, 'canceled', {'source': 'user'})
    elif args.command == 'recover':
        from .recovery import recover
        scope = store.get(args.id)['scope']
        registry = ProjectTools(scope['project'], store, args.id, scope.get('skills'))
        print(json.dumps(recover(store,args.id,registry),ensure_ascii=False,indent=2))
    elif args.command == 'run':
        scope = store.get(args.id)['scope']
        registry = ProjectTools(scope['project'], store, args.id, scope.get('skills'))
        Agent(store, Provider(), registry).run(args.id, args.steps)
    print(json.dumps(store.get(args.id), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
