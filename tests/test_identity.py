from pathlib import Path
import ast,json,subprocess,os,sys
import pytest
from snap3d.common import digest



def test_standalone_import():
    env=dict(os.environ,PYTHONPATH=str(Path('.').resolve()))
    subprocess.run([sys.executable,'-c','import sys; import snap3d.vendor; import snap3d.vendor.rigid_flex; assert not any(k == "core" or k.startswith("core.") or k == "adsl" or k.startswith("adsl.") for k in sys.modules)'],env=env,check=True)

def test_vendor_function_ast_matches_recorded_origin():
    # Source-derived ASTs, including the explicitly recorded dependency import
    # relocation in xml_model; its original full AST is verified separately.
    record=json.loads(Path('snap3d/vendor/function_ast.json').read_text())
    import hashlib
    for filename,functions in record.items():
        tree=ast.parse(Path('snap3d/vendor',filename).read_text())
        actual={n.name:hashlib.sha256(ast.dump(n,include_attributes=False).encode()).hexdigest() for n in tree.body if isinstance(n,ast.FunctionDef)}
        for name,expected in functions.items():assert actual[name]==expected
