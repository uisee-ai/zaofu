from pathlib import Path
import json, subprocess, sys
root=Path(sys.argv[1]).resolve()
results=[]
for args, expected in [([], "Hello, world!\n"), (["--name", "ZaoFu"], "Hello, ZaoFu!\n")]:
    p=subprocess.run([sys.executable, str(root/"hello.py"), *args],cwd=root,capture_output=True,text=True,timeout=15)
    results.append({"args":args,"stdout":p.stdout,"stderr":p.stderr,"exit_code":p.returncode,"passed":p.returncode==0 and p.stdout==expected})
print(json.dumps({"root":str(root),"cases":results,"passed":all(x["passed"] for x in results)},ensure_ascii=False))
raise SystemExit(0 if all(x["passed"] for x in results) else 1)
