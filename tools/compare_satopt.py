#!/usr/bin/env python3
"""Compare a preserved pre-SAT ledger snapshot with named measured runs."""
from __future__ import annotations

import argparse
from collections import Counter
import datetime
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from lhdtrack.ledger import Ledger
from lhdtrack.report.html import write_report

FLOWS = ('syn_lhd_pyrope', 'syn_lhd_verilog')
METRICS = ('delay', 'area', 'cells', 'depth', 'time', 'mem')


def key(row):
    return row['test'], row.get('config', 'default'), row.get('tech'), row['flow']


def values(row):
    qor, sta = row.get('qor', {}), row.get('sta', {})
    return dict(zip(METRICS, (
        sta.get('opensta_ns'), qor.get('area_um2'), qor.get('cells'),
        qor.get('logic_depth'), row.get('time_ms', {}).get('total'),
        row.get('peak_rss_kb', {}).get('max'),
    )))


def positive(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--baseline-html', type=Path, required=True)
    parser.add_argument('--before-toolchain', type=Path, required=True)
    parser.add_argument('--after-toolchain', type=Path, required=True)
    parser.add_argument('--run', action='append', required=True)
    parser.add_argument('--host', default='mascm1')
    parser.add_argument('--profile', choices=['satopt', 'noctrl'], default='satopt')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--synthesis-run')
    args = parser.parse_args()
    before_doc = json.loads(args.before.read_text())
    before = {key(r): r for r in before_doc if r.get('host') == args.host}
    rows = Ledger(ROOT).load(args.host)
    after = {key(r): r for r in rows if r.get('run_id') in args.run}
    if not after:
        raise SystemExit('no rows for the requested run IDs')
    old_tc = json.loads(args.before_toolchain.read_text())
    new_tc = json.loads(args.after_toolchain.read_text())
    old_libs = {k: v['sha256'] for k, v in old_tc['tech'].items()}
    new_libs = {k: v['sha256'] for k, v in new_tc['tech'].items()}
    if old_tc['host_class'] != new_tc['host_class'] or old_libs != new_libs:
        raise SystemExit('host class or Liberty mismatch: refusing comparison')

    pairs, coverage, summary = [], [], []
    for slot, new in sorted(after.items()):
        if new['flow'] not in FLOWS or slot not in before:
            continue
        old = before[slot]
        if old['host_class'] != new['host_class']:
            raise SystemExit(f'host class mismatch for {slot}')
        if old['status'] != 'ok' or new['status'] != 'ok':
            continue
        ov, nv = values(old), values(new)
        ratios = {m: ov[m] / nv[m] for m in METRICS if positive(ov[m]) and positive(nv[m])}
        proof = after.get((*slot[:3], 'lec_netlist'), {})
        old_proof = before.get((*slot[:3], 'lec_netlist'), {})
        proof_fields = ('lec_result', 'lec_aux_result', 'lec_verilog_result')
        before_refuted = any(old_proof.get(f, {}).get('verdict') == 'refuted' for f in proof_fields)
        after_refuted = any(proof.get(f, {}).get('verdict') == 'refuted' for f in proof_fields)
        refuted = before_refuted or after_refuted
        headline = (new['flow'] == 'syn_lhd_pyrope' and old.get('comparable')
                    and new.get('comparable') and not refuted
                    and proof.get('lec_result', {}).get('verdict') == 'proven')
        pairs.append(dict(test=slot[0], config=slot[1], tech=slot[2], flow=slot[3],
                          ratios=ratios, before=ov, after=nv, headline=bool(headline),
                          netlist_refuted=refuted,
                          before_netlist_refuted=before_refuted,
                          after_netlist_refuted=after_refuted,
                          satopt_facts=new.get('qor', {}).get('satopt_facts', 0),
                          before_run=old['run_id'],
                          after_run=new['run_id']))
    for tech in sorted(new_libs):
        for flow in FLOWS:
            slots = [k for k in after if k[2:] == (tech, flow)]
            coverage.append(dict(
                tech=tech, flow=flow,
                before_ok=sum(before.get(k, {}).get('status') == 'ok' for k in slots),
                after_ok=sum(after[k]['status'] == 'ok' for k in slots),
                new_failures=sum(before.get(k, {}).get('status') == 'ok'
                                 and after[k]['status'] != 'ok' for k in slots),
                recovered=sum(before.get(k, {}).get('status') != 'ok'
                              and after[k]['status'] == 'ok' for k in slots),
            ))
            for population in ('Headline Pyrope', 'All matched, excluding refuted netlists'):
                group = [r for r in pairs if r['tech'] == tech and r['flow'] == flow
                         and not r['netlist_refuted']
                         and (population != 'Headline Pyrope' or r['headline'])]
                if not group:
                    continue
                ratios, counts = {}, {}
                for metric in METRICS:
                    points = [r['ratios'][metric] for r in group if metric in r['ratios']]
                    counts[metric] = len(points)
                    ratios[metric] = (math.exp(sum(map(math.log, points)) / len(points))
                                      if points else None)
                summary.append(dict(tech=tech, flow=flow, population=population,
                                    n=len(group), counts=counts, ratios=ratios))
    verdicts = {
        field: dict(Counter(r.get(field, {}).get('verdict', 'missing')
                            for r in after.values() if r['flow'] == 'lec_netlist'))
        for field in ('lec_result', 'lec_aux_result', 'lec_verilog_result')
    }
    if args.profile == 'noctrl':
        for slot, row in after.items():
            if row['flow'] not in FLOWS or row['status'] != 'ok':
                continue
            policy = row.get('qor', {}).get('synth_policy', {})
            if policy.get('pass.color.ctrl_cones') != 'false' or policy.get('pass.abc.satopt') != 'true':
                raise SystemExit(f'noctrl profile settings mismatch for {slot}')
            old_policy = before.get(slot, {}).get('qor', {}).get('synth_policy', {})
            if before.get(slot, {}).get('status') == 'ok':
                if old_policy.get('pass.color.ctrl_cones') != 'true':
                    raise SystemExit(f'baseline is not the control-cones profile for {slot}')
                strip_ctrl = lambda p: {k: v for k, v in p.items() if k != 'pass.color.ctrl_cones'}
                if strip_ctrl(old_policy) != strip_ctrl(policy):
                    raise SystemExit(f'other synthesis settings changed for {slot}')
        if args.out is None or args.synthesis_run is None:
            raise SystemExit('noctrl requires --out and --synthesis-run to preserve the default report')
    doc = dict(
        schema_version=1, host=args.host, date=datetime.date.today().isoformat(),
        baseline_html=args.baseline_html.name,
        baseline_sha256=hashlib.sha256(args.baseline_html.read_bytes()).hexdigest(),
        run_ids=args.run, liberty_match=True, liberty_hashes=new_libs,
        summary=summary, coverage=coverage, pairs=pairs, netlist_verdicts=verdicts,
        notes=[
            'Both synthesis front ends use pass.abc.satopt=true and one ABC worker.',
            'This before/after workspace comparison can include other compiler or '
            'coloring changes since the preserved report; it is not a SAT-only attribution.',
            'Pairs with a refuted netlist on either side are excluded from aggregate QoR. Unproved results remain '
            'visible in the exploratory population and the equivalence tables below.',
        ],
    )
    if args.profile == 'noctrl':
        doc['title'] = 'Control cones disabled · SAT optimization enabled'
        doc['ratio_label'] = 'ctrl_cones=true / ctrl_cones=false'
        doc['notes'] = [
            'Both profiles enable pass.abc.satopt and use one ABC worker. The comparison '
            'changes pass.color.ctrl_cones from true to false on the recorded toolchain.',
            'Pairs with a refuted netlist on either side are excluded from aggregate QoR. Unproved results remain '
            'visible in the exploratory population and equivalence tables.',
        ]
    expected_proofs = len({slot[:3] for slot in after if slot[3] in FLOWS})
    recorded_proofs = sum(r['flow'] == 'lec_netlist' for r in after.values())
    doc['equivalence_jobs'] = {'expected': expected_proofs, 'recorded': recorded_proofs}
    if recorded_proofs < expected_proofs:
        doc['title'] = doc.get('title', 'SAT optimization · preserved-report comparison') + ' (netlist checks pending)'
        doc['notes'].append(f'Fresh netlist evaluation is pending: {recorded_proofs} of '
                            f'{expected_proofs} jobs have finalized ledger rows.')
    path = ROOT / 'data' / f'{args.profile}-{args.host}-comparison.json'
    path.write_text(json.dumps(doc, indent=2) + '\n')
    write_report(ROOT, host=args.host, out=args.out, synthesis_run=args.synthesis_run,
                 comparison_name=args.profile)
    print(json.dumps(dict(summary=summary, coverage=coverage, verdicts=verdicts), indent=2))
    print(f'wrote {path}')


if __name__ == '__main__':
    main()
