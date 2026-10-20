#!/usr/bin/env python3
"""Render retained DEF/LEF cell footprints to PNG without running placement.

Uses matplotlib only for this optional review-image export. Blue rectangles are
standard cells and orange rectangles are macros; no routing or timing is inferred.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from lhdtrack.ledger import Ledger


def render(physical: Path, title: str) -> None:
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    from matplotlib.collections import PatchCollection
    from matplotlib.patches import Rectangle

    masters = {}
    for name in json.loads((physical / 'lefs.json').read_text()):
        text = (physical / name).read_text()
        for macro in re.finditer(r'\bMACRO\s+(\S+)(.*?)(?=\bMACRO\s|\Z)', text, re.S):
            size = re.search(r'\bSIZE\s+([\d.eE+-]+)\s+BY\s+([\d.eE+-]+)', macro[2])
            if size:
                masters[macro[1]] = (*map(float, size.groups()),
                                    bool(re.search(r'\bCLASS\s+BLOCK\b', macro[2])))
    text = (physical / 'out.def').read_text()
    units = float(re.search(r'UNITS DISTANCE MICRONS\s+(\d+)', text)[1])
    die = re.search(r'DIEAREA\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*'
                    r'\(\s*(-?\d+)\s+(-?\d+)\s*\)', text)
    if not die:
        raise ValueError('PNG export requires a rectangular DEF die')
    xlo, ylo, xhi, yhi = [int(v) / units for v in die.groups()]
    components = re.search(r'\bCOMPONENTS\s+(\d+)\s*;(.*?)END COMPONENTS', text, re.S)
    patches, colors = [], []
    for component in components[2].split(';'):
        if not component.strip():
            continue
        master = re.match(r'\s*-\s+\S+\s+(\S+)', component)[1]
        placement = re.search(r'\+\s+(?:PLACED|FIXED|COVER)\s*\(\s*(-?\d+)\s+'
                              r'(-?\d+)\s*\)\s*(\S+)', component)
        if not placement:
            raise ValueError(f'unplaced component in post-place DEF: {master}')
        width, height, macro = masters[master]
        if placement[3] in ('E', 'W', 'FE', 'FW'):
            width, height = height, width
        x, y = int(placement[1]) / units, int(placement[2]) / units
        patches.append(Rectangle((x, y), width, height))
        colors.append('#d97706' if macro else '#2563eb')
    if len(patches) != int(components[1]):
        raise ValueError('DEF component count differs from rendered footprints')
    fig, ax = plt.subplots(figsize=(8, 8), layout='constrained')
    ax.add_collection(PatchCollection(patches, facecolors=colors, edgecolors='#172554',
                                      linewidths=.12))
    ax.set(xlim=(xlo, xhi), ylim=(ylo, yhi), xlabel='µm', ylabel='µm', aspect='equal',
           title=f'{title}\nPre-CTS · {len(patches):,} placed footprints')
    ax.set_facecolor('#f8fafc')
    fig.savefig(physical / 'post-place.png', dpi=160)
    plt.close(fig)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    for row in Ledger(ROOT).load():
        if row.get('placement_run') and row['run_id'] == args.run \
                and row['flow'].startswith('place_') and row['status'] == 'ok':
            render(Path(row['placement_work']) / 'physical', f'{row["test"]}\n{row["flow"]}')
