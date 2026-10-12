#!/usr/bin/env python3
"""sweep.py SPEC_JSON OUT.json   (env: JOBS TMO LEC TESTS)
SPEC: {"tag": "attrs"}; attrs go on the TOP decl; "ALL:attrs" goes on every module decl ({name} expands)."""
import concurrent.futures as cf, json, os, pathlib, re, subprocess, sys
here = pathlib.Path(__file__).parent
TRK = pathlib.Path("/mada/users/renau/projs/lhdtrack/tests")
VAR = pathlib.Path("/var/tmp/renau/exp/variants")
spec = json.loads(sys.argv[1]); out = pathlib.Path(sys.argv[2])
jobs = int(os.environ.get("JOBS", "24")); timeout = int(os.environ.get("TMO", "300"))
only = os.environ.get("TESTS"); lec = os.environ.get("LEC", "1") == "1"
DECL = re.compile(r"(\b(?:comb|mod|pipe)\s+)([A-Za-z_][A-Za-z0-9_]*)(?:::\[([^\]]*)\])?\s*\(")
tests = []
for d in sorted(TRK.iterdir()):
    m = re.search(r'^top\s*=\s*"([^"]+)"', (d/"design.toml").read_text(), re.M) if (d/"design.toml").exists() else None
    if m and (d/"pyrope"/f"{m.group(1)}.prp").exists() and (d/"constraints"/"asap7.sdc").exists():
        tests.append((d.name, m.group(1)))
if only: tests = [t for t in tests if t[0] in only.split(",")]

def one(job):
    test, top, tag, attrs = job
    env = dict(os.environ, EXP_ROOT="/var/tmp/renau/exp/runs", TMPDIR="/var/tmp/renau/exp/tmp")
    args = [sys.executable, str(here/"syn.py"), test, "--tag", tag, "--timeout", str(timeout)]
    if attrs:
        d = VAR/test/tag
        subprocess.run(["rm", "-rf", str(d)]); d.mkdir(parents=True)
        every = attrs.startswith("ALL:") or attrs.startswith("OPS:")
        kind = None; body = attrs
        if attrs.startswith("ALL:"): body = attrs[4:]
        if attrs.startswith("OPS:"):
            _, kind, body = attrs.split(":", 2)
        n_decl = 0
        OPRX = {"mult": r"[A-Za-z0-9_\)\]]\s?\*\s?[A-Za-z_\(0-9]", "add": r"[A-Za-z0-9_\)\]]\s[+\-]\s[A-Za-z_\(0-9]|\bsum\b", "shift": r"<<|>>"}
        for f in (TRK/test/"pyrope").glob("*.prp"):
            text = re.sub(r"//[^\n]*", "", f.read_text())
            if kind and not re.search(OPRX[kind], text):
                (d/f.name).write_text(f.read_text()); continue
            def patch(m, f=f):
                name = m.group(2)
                if not every and (f.stem != top or name != top): return m.group(0)
                a = body.replace("{name}", name)
                inner = f"{m.group(3)},{a}" if m.group(3) is not None else a
                return f"{m.group(1)}{name}::[{inner}]("
            new_src, k = DECL.subn(patch, f.read_text())
            n_decl += k if (every or f.stem == top) else 0
            (d/f.name).write_text(new_src)
        if n_decl == 0: return dict(test=test, tag=tag, status="nodecl")
        args += ["--prp", str(d/f"{top}.prp")]
    for x in os.environ.get("SETS", "").split():
        args += ["--set", x]
    if lec: args += ["--lec"]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout*3, env=env)
    except subprocess.TimeoutExpired:
        return dict(test=test, tag=tag, status="timeout")
    line = (p.stdout.strip().splitlines() or [""])[-1]
    m = re.search(r"cells=(\S+) area=(\S+) sta=(\S+?)(?:ps)? abc=(\S+) regions=(\d+) trials=(\d+) lec=(\S+) ([0-9.]+)s", line)
    if p.returncode or not m: return dict(test=test, tag=tag, status="fail", msg=line[-240:])
    c, a, s, ab, r, t, l, dt = m.groups()
    f = lambda x: None if x in ("None", "") else float(x)
    return dict(test=test, tag=tag, status="ok", cells=f(c), area=f(a), sta=f(s), abc=f(ab), regions=int(r), trials=int(t), lec=l, secs=float(dt))

work = [(t, top, tag, attrs) for t, top in tests for tag, attrs in spec.items()]
res = []
with cf.ThreadPoolExecutor(jobs) as ex:
    for i, r in enumerate(ex.map(one, work)):
        res.append(r)
        if (i+1) % 40 == 0: print(f"  {i+1}/{len(work)}", flush=True)
out.write_text(json.dumps(res, indent=1)); print("wrote", out, len(res))
