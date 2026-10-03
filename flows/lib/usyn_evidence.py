"""Retain additive native decision evidence separately from mapped cell QoR."""
from hashlib import sha256
import json
from pathlib import Path


def native_evidence(path: Path) -> dict:
    data = path.read_bytes()
    report = json.loads(data)
    regions = report.get("regions", [])

    def totals(field):
        out = {}
        for region in regions:
            for name, value in region.get(field, {}).items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    out[name] = out.get(name, 0) + value
        return out

    result = {name: report[name] for name in
              ("schema_version", "kind", "algorithm", "scope", "tmap", "output", "totals",
               "constraints", "endpoint_search", "elapsed_ms", "peak_bytes", "cache")
              if name in report}
    result.update(report_sha256=sha256(data).hexdigest(), definition_regions=len(regions),
                  cost_scope="definition-region native proxy; not mapped cell area",
                  cost_stages={name: totals(name) for name in
                               ("before", "after_pairs", "after_residual", "after")},
                  work=totals("work"), residual=totals("residual"),
                  residual_accepted_regions=sum(bool(r.get("residual", {}).get("accepted"))
                                                for r in regions),
                  residual_skipped_regions=sum(bool(r.get("residual", {}).get("skipped"))
                                               for r in regions),
                  search_exhausted_regions=sum(bool(r.get("search_exhausted")) for r in regions),
                  identity_fallbacks=sum(r.get("identity_fallbacks", 0) for r in regions))
    return result
