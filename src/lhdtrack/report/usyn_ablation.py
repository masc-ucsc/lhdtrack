"""Pair native USYN stages with a retained ABC run without hiding failures."""
from collections import Counter
import math

from .html import _e, _page
from .verilog_eval import lec_flow, proof_covers_digest, synth_flow


def unbounded_proof(synth, proof):
    """Require this exact emission and reject every conflicting checker result."""
    blocks = [proof.get(f, {}) for f in ("lec_verilog_result", "lec_aux_result")]
    if any(b.get("verdict") in {"refuted", "error"} for b in blocks):
        return False
    digest = synth.get("qor", {}).get("netlist_sha256")
    return bool(digest) and any(
        b.get("verdict") == "proven" and not b.get("bounded", False)
        and proof_covers_digest(b, digest) for b in blocks)


def frequency_ratio(base, measured, base_proof, measured_proof):
    """Only complete, equally constrained, proven mappings yield a speedup."""
    if not all(unbounded_proof(s, p) for s, p in
               ((base, base_proof), (measured, measured_proof))):
        return None
    if any(s.get("status") != "ok" for s in (base, measured)):
        return None
    identity = ("host", "host_class", "liberty_sha256", "tech")
    if any(not base.get(k) or base.get(k) != measured.get(k) for k in identity):
        return None
    b, m = base.get("sta", {}), measured.get("sta", {})
    if any(s.get("opensta_metric") != "sdc-minimum-period" for s in (b, m)):
        return None
    if (b.get("time_unit"), b.get("sdc_period_ns")) != (
            m.get("time_unit"), m.get("sdc_period_ns")):
        return None
    for field in ("sdc_sha256", "source_sha256"):
        digests = [s.get("qor", {}).get(field) for s in (base, measured)]
        if not all(digests) or digests[0] != digests[1]:
            return None
    delays = b.get("opensta_ns"), m.get("opensta_ns")
    if not all(isinstance(x, (int, float)) and math.isfinite(x) and x > 0 for x in delays):
        return None
    return delays[0] / delays[1]


def summarize(history, specs):
    """Every named slot remains in the matrix, even without a synthesis result."""
    hosts = {(s["host"], s["tech"], s["liberty_sha256"]) for s in specs.values()}
    if len(hosts) != 1:
        raise ValueError("ablation runs must use the same host, technology and Liberty")
    runs = {s["run_id"] for s in specs.values()}
    rows = {(r["run_id"], r["test"], r["config"], r["flow"]): r
            for r in history if r["run_id"] in runs}
    slots = sorted({(r["test"], r["config"]) for s in specs.values() for r in s["slots"]})
    counts = {name: Counter() for name in specs}
    samples = {name: [] for name in specs}
    common_samples = {name: [] for name in specs}
    iteration_samples = []
    matrix = []
    for test, config in slots:
        entry = dict(test=test, config=config, variants={})
        base_run = specs["abc"]["run_id"]
        base = rows.get((base_run, test, config, synth_flow("abc", False)), {})
        base_proof = rows.get((base_run, test, config, lec_flow("abc", False)), {})
        for name, spec in specs.items():
            mapper = "abc" if name == "abc" else "usyn"
            synth = rows.get((spec["run_id"], test, config, synth_flow(mapper, False)), {})
            proof = rows.get((spec["run_id"], test, config, lec_flow(mapper, False)), {})
            ratio = frequency_ratio(base, synth, base_proof, proof)
            verified = unbounded_proof(synth, proof)
            counts[name][synth.get("status", "pending")] += 1
            counts[name]["unbounded_proven" if verified else "unverified"] += 1
            # The language headline policy remains idiomatic and LEC-proven.
            if ratio is not None and synth.get("comparable"):
                samples[name].append(math.log(ratio))
            entry["variants"][name] = dict(
                synthesis=synth, proof=proof, frequency_ratio=ratio, unbounded_proven=verified)
        variants = entry["variants"]
        if all(v["frequency_ratio"] is not None and v["synthesis"].get("comparable")
               for v in variants.values()):
            for name, v in variants.items():
                common_samples[name].append(math.log(v["frequency_ratio"]))
        if "feedback" in variants and "improved" in variants:
            before, after = variants["feedback"], variants["improved"]
            ratio = frequency_ratio(before["synthesis"], after["synthesis"],
                                    before["proof"], after["proof"])
            if ratio is not None and all(v["synthesis"].get("comparable")
                                         for v in (before, after)):
                iteration_samples.append(math.log(ratio))
        matrix.append(entry)
    return dict(schema_version=1, runs=specs, rows=matrix,
                common_headline={k: dict(count=len(v), frequency_ratio=
                                 math.exp(math.fsum(v) / len(v)) if v else None)
                                 for k, v in common_samples.items()},
                native_iteration=dict(count=len(iteration_samples), frequency_ratio=
                                 math.exp(math.fsum(iteration_samples) / len(iteration_samples))
                                 if iteration_samples else None),
                counts={k: dict(v) for k, v in counts.items()},
                headline={k: dict(count=len(v), frequency_ratio=
                          math.exp(math.fsum(v) / len(v)) if v else None)
                          for k, v in samples.items()})


def render(summary):
    names = list(summary["runs"])
    body = '<h1>ASAP7 native USYN ablation</h1><p>Exact emitted-netlist LEC; '
    body += 'frequency ratios are ABC / USYN minimum period. Bounded proofs and '
    body += 'missing timing stay visible and do not enter frequency comparisons. '
    body += 'Headline geomeans include only idiomatic, LEC-proven Pyrope slots.</p>'
    body += '<table><thead><tr><th>Design/config</th>'
    for name in names:
        body += f'<th>{_e(name)}: period / area / proof / ratio</th>'
    body += '</tr></thead><tbody>'
    for row in summary["rows"]:
        body += f'<tr><td>{_e(row["test"] + "/" + row["config"])}</td>'
        for name in names:
            variant = row["variants"][name]
            synth, proof = variant["synthesis"], variant["proof"]
            sta, qor = synth.get("sta", {}), synth.get("qor", {})
            verdicts = '/'.join(str(proof.get(f, {}).get("verdict", "pending"))
                                + ("(bounded)" if proof.get(f, {}).get("bounded") else "")
                                for f in ("lec_verilog_result", "lec_aux_result"))
            ratio = variant["frequency_ratio"]
            text = (f'{sta.get("opensta_ns", "—")} {sta.get("time_unit", "")} / '
                    f'{qor.get("area_um2", "—")} / {verdicts} / '
                    + (f'{ratio:.3f}×' if ratio is not None else '—'))
            body += f'<td title="{_e(synth.get("note", synth.get("status", "pending")))}">'
            body += _e(text) + '</td>'
        body += '</tr>'
    body += '</tbody><tfoot>'
    for key, label in (("headline", "Geomean vs ABC"),
                       ("common_headline", "Same-slot geomean vs ABC")):
        if key not in summary:
            continue
        body += f'<tr><th>{label}</th>'
        for name in names:
            metric = summary[key][name]
            ratio = metric["frequency_ratio"]
            value = f'{ratio:.3f}×' if ratio is not None else '—'
            body += f'<td>{value} (n={metric["count"]})</td>'
        body += '</tr>'
    body += '</tfoot></table>'
    iteration = summary.get("native_iteration", {})
    if iteration.get("frequency_ratio") is not None:
        body += (f'<p>Improved native frequency / feedback-stage frequency: '
                 f'{iteration["frequency_ratio"]:.3f}× (n={iteration["count"]}, '
                 'paired proven idiomatic slots).</p>')
    return _page('ASAP7 USYN ablation', body)
