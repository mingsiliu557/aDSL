"""Static check of generated source only, not library implementation."""
import ast
from pathlib import Path

DISABLED_PROFILE_APIS = frozenset({'Polygon', 'linear_extrude', 'rotate_extrude', 'loft', 'hull'})

class DisabledProfileAPI(ValueError):
    def __init__(self, name, line):
        self.name, self.line = name, line
        super().__init__(f'ABLATION_DISABLED_API: {name}; use basic primitives and Boolean operations')


def check_profile_source(text, *, filename='<generated>'):
    tree = ast.parse(text, filename=filename)
    modules = {'adsl.core'}
    star = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and (node.module == 'adsl.core' or node.module.startswith('adsl.core.')):
                for item in node.names:
                    if item.name == '*': star = True
                    elif item.name in DISABLED_PROFILE_APIS:
                        # Importing a disabled API is itself outside this variant.
                        raise DisabledProfileAPI(item.name, node.lineno)
            elif node.module == 'adsl':
                modules.update(item.asname or item.name for item in node.names if item.name == 'core')
        elif isinstance(node, ast.Import):
            modules.update(item.asname or item.name for item in node.names if item.name == 'adsl.core' or item.name.startswith('adsl.core.'))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and star and node.id in DISABLED_PROFILE_APIS:
            raise DisabledProfileAPI(node.id, node.lineno)
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and node.attr in DISABLED_PROFILE_APIS:
            base = ast.unparse(node.value)
            if base in modules or base.startswith('adsl.core.'):
                raise DisabledProfileAPI(node.attr, node.lineno)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'getattr'
                and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in DISABLED_PROFILE_APIS):
            raise DisabledProfileAPI(node.args[1].value, node.lineno)


from contextlib import contextmanager

@contextmanager
def disabled_profile_runtime(source):
    import adsl.core as core
    import adsl.core.constructive as constructive
    import inspect
    saved = [(module, name, getattr(module, name))
             for module in (core, constructive) for name in DISABLED_PROFILE_APIS]
    def blocked(name):
        def call(*args, **kwargs):
            frames = [f for f in inspect.stack() if Path(f.filename).resolve() == source.resolve()]
            raise DisabledProfileAPI(name, frames[0].lineno if frames else 1)
        return call
    try:
        for module, name, original in saved:
            setattr(module, name, blocked(name))
        yield
    finally:
        for module, name, original in saved:
            setattr(module, name, original)
