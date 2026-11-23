"""Tool plugin loader."""
import importlib
import pkgutil


def load_tool_plugins(registry, config=None, package="gantry.tool_plugins"):
    config = config or {}

    disabled = set(config.get('tool_plugins', {}).get('disabled', []))
    report = {'loaded': [], 'disabled': [], 'failed': [], 'tools': []}
    package_module = importlib.import_module(package)
    for info in pkgutil.iter_modules(getattr(package_module, chr(95) * 2 + "path" + chr(95) * 2)):
        name = info.name
        if name.startswith('_'):
            continue
        if name in disabled:
            report['disabled'].append(name)
            continue
        tools = getattr(registry, '_tools', {})
        before = dict(tools)
        # register() 不止写 _tools：还会覆盖 _extra_descriptions（同名工具的最新
        # 描述）与 _approval_extra（approval=True 声明）。加载失败回滚时这三处
        # 必须一起恢复，否则半注册的插件会留下幽灵描述/审批标记。
        descs_before = list(getattr(registry, '_extra_descriptions', []))
        approval_before = set(getattr(registry, '_approval_extra', set()))
        try:
            module = importlib.import_module(f'{package}.{name}')
            register = getattr(module, 'register')
            register(registry)
            duplicates = [key for key, value in before.items()
                          if tools.get(key) is not value]
            if duplicates:
                raise ValueError(f'duplicate tool names: {duplicates}')
            added = sorted(set(tools) - set(before))
            report['loaded'].append(name)
            report['tools'].extend(added)
        except Exception as exc:
            for tool_name in set(tools) - set(before):
                registry.unregister(tool_name)
            for tool_name, fn in before.items():
                tools[tool_name] = fn
            registry._extra_descriptions = descs_before
            registry._approval_extra = approval_before
            report['failed'].append({'plugin': name, 'error': str(exc)})
    return report
