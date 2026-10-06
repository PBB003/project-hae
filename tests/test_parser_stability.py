"""Regresión de cierre nativo: siempre en un proceso aislado y sin diálogos."""
import json
import subprocess
import sys


def test_parser_survives_repeated_trees_and_forced_gc():
    script = r'''
import ctypes,gc,sys,json
if sys.platform == "win32":
    ctypes.windll.kernel32.SetErrorMode(3)
from client import ast_parser
assert ast_parser.AVAILABLE, ast_parser.INIT_ERROR
source = """interface Props { title: string; value?: boolean; url?: \"https://example.invalid/a;b\"; }
export function Panel({title,value}: Props) { return <div>{title}</div>; }
export function usePanel<T>(initial: T): T { return initial; }
"""
gc.set_threshold(20,5,5)
for n in range(1200):
    components,utilities = ast_parser.extract(source,".tsx","src/Panel.tsx",True,True,include_contracts=True)
    assert components[0]["name"] == "Panel"
    assert any(item["name"] == "usePanel" for item in utilities)
    if n % 10 == 0:
        gc.collect()
print(json.dumps({"iterations":1200,"passed":True}))
'''
    result = subprocess.run([sys.executable, '-c', script],capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, f"Parser cerrado: {result.returncode}; {result.stderr[-1500:]}"
    assert json.loads(result.stdout)['passed'] is True
