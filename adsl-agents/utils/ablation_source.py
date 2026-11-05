"""Static check of generated source only, not library implementation."""
import ast

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
            if node.module == 'adsl.core':
                for item in node.names:
                    if item.name == '*': star = True
                    elif item.name in DISABLED_PROFILE_APIS:
                        # Importing a disabled API is itself outside this variant.
                        raise DisabledProfileAPI(item.name, node.lineno)
            elif node.module == 'adsl':
                modules.update(item.asname or item.name for item in node.names if item.name == 'core')
        elif isinstance(node, ast.Import):
            modules.update(item.asname or item.name for item in node.names if item.name == 'adsl.core')
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call): continue
        func = node.func
        if isinstance(func, ast.Name) and star and func.id in DISABLED_PROFILE_APIS:
            raise DisabledProfileAPI(func.id, node.lineno)
        if isinstance(func, ast.Attribute) and func.attr in DISABLED_PROFILE_APIS:
            if ast.unparse(func.value) in modules:
                raise DisabledProfileAPI(func.attr, node.lineno)
