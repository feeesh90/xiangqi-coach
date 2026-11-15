from pathlib import Path
s=Path("engine.py").read_text(encoding="utf-8")
assert 'go {mode} {value}' in s
assert 'mode not in ("depth","nodes")' in s
assert 'setoption name Threads' in s
assert 'setoption name Hash' in s
print("ENGINE_GO_MODES_OK")
