#!/usr/bin/env python3
"""syn.py TEST [--prp FILE] [--tag T] [--set k=v ...]  -> one-line QoR (asap7, lhd abc)."""
import argparse, json, os, pathlib, re, subprocess, sys, time
LHD = os.environ.get("LHD", "/var/tmp/renau/_bazel_renau/8697baa9513f6ce44c9d515478fdd6ff/execroot/_main/bazel-out/k8-opt/bin/lhd/lhd")
TRK = pathlib.Path("/mada/users/renau/projs/lhdtrack")
LIB = TRK / "asap7sc7p5t_RVT_TT_merged.lib"
ROOT = pathlib.Path(os.environ.get("EXP_ROOT", "/tmp/claude-3052/exp"))
ap = argparse.ArgumentParser()
ap.add_argument("test"); ap.add_argument("--prp"); ap.add_argument("--tag", default="base")
ap.add_argument("--set", action="append", default=[]); ap.add_argument("--mapper", default="abc")
ap.add_argument("--timeout", type=int, default=1800); ap.add_argument("--lec", action="store_true"); ap.add_argument("--lec-timeout", default="120")
a = ap.parse_args()
t = TRK / "tests" / a.test
top = re.search(r'^top\s*=\s*"([^"]+)"', (t / "design.toml").read_text(), re.M).group(1)
prp = pathlib.Path(a.prp).resolve() if a.prp else (t / "pyrope" / f"{top}.prp")
period = re.search(r"-period\s+([0-9.]+)", (t / "constraints" / "asap7.sdc").read_text()).group(1)
w = ROOT / a.test / a.tag; w.mkdir(parents=True, exist_ok=True)
for d in ("W", "netlist"):
    subprocess.run(["rm", "-rf", str(w / d)])
cmd = [LHD, "synth", str(prp), "--top", top, "--workdir", str(w / "W"), "--emit-dir", f"lg:{w}/netlist",
       "--result-json", str(w / "synth.json"), "--stats",
       "--set", f"synth.liberty={LIB}", "--set", f"abc.delay={period}", "--set", f"synth.mapper={a.mapper}"]
for s in a.set: cmd += ["--set", s]
t0 = time.time()
p = subprocess.run(cmd, capture_output=True, text=True, timeout=a.timeout, cwd=w)
dt = time.time() - t0
(w / "stdout.txt").write_text(p.stdout); (w / "stderr.txt").write_text(p.stderr)
if p.returncode != 0:
    print(f"{a.test:28s} {a.tag:14s} FAIL rc={p.returncode} {dt:5.1f}s :: {(p.stdout+p.stderr).strip().splitlines()[-1][:160] if (p.stdout+p.stderr).strip() else ''}")
    sys.exit(1)
d = json.loads((w / "synth.json").read_text())
q = d.get("qor", {}); ab = q.get("abc", {}); tot = ab.get("total", {}); sta = (q.get("sta") or {}).get("designs") or [{}]
regs = ab.get("regions", [])
lec = "-"
if a.lec:
    models = ROOT / "models"
    if not models.exists():
        subprocess.run([LHD, "pass", "liberty", "gensim", str(LIB), "--emit-dir", f"lg:{models}", "-q"], capture_output=True)
    ref = t / "pyrope" / f"{top}.prp"   # ORIGINAL, unannotated source
    r = subprocess.run([LHD, "lec", "--impl", f"lg:{w}/netlist", "--ref", str(ref), "--lib", f"lg:{models}", "--top", f"{top}.{top}",
                        "--workdir", str(w / "lec"), "--set", f"formal.timeout={a.lec_timeout}", "--result-json", str(w / "lec.json"), "-q"],
                       capture_output=True, text=True, timeout=a.timeout)
    try: lec = json.loads((w / "lec.json").read_text())["lec"]["verdict"]
    except Exception: lec = f"err{r.returncode}"
print(f"{a.test:26s} {a.tag:16s} cells={sta[0].get('cells', tot.get('gates'))} area={sta[0].get('area', tot.get('area'))} "
      f"sta={sta[0].get('max_delay')}ps abc={tot.get('max_delay')} regions={len(regs)} trials={sum(r.get('ware_trials',0) for r in regs)} lec={lec} {dt:.1f}s")
