import argparse
import json
import math
import random
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


FORMAT_SLUGS_BY_TIER: dict[str, str] = {
    "AG": "anythinggoes",
    "Uber": "ubers",
    "OU": "ou",
    "UU": "uu",
    "RU": "ru",
    "NU": "nu",
    "PU": "pu",
    "ZU": "zu",
}


def _dot(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    s = 0.0
    for k, v in a.items():
        bv = b.get(k)
        if bv is not None:
            s += v * bv
    return s


def _l2_norm(a: dict[str, float]) -> float:
    return math.sqrt(sum(v * v for v in a.values()))


def _normalize(a: dict[str, float]) -> dict[str, float]:
    n = _l2_norm(a)
    if n <= 0.0:
        return {}
    return {k: v / n for k, v in a.items() if v != 0.0}


def _parse_elo_from_filename(filename: str) -> int | None:
    m = re.search(r"-(\d+)\.json$", filename, flags=re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1))


@dataclass(frozen=True)
class TierSource:
    month: str
    tier: str
    elo: int | None
    file_path: Path


@dataclass(frozen=True)
class ClusterResult:
    source: TierSource
    k: int
    clusters: dict[int, list[str]]
    soft_memberships: dict[str, list[tuple[int, float]]]
    cluster_top_features: dict[int, dict[str, list[tuple[str, float]]]]


class TierRoleClusterer:
    def __init__(
        self,
        *,
        movesets_json_dir: str | Path = Path(__file__).resolve().parent / "movesets_json",
        random_seed: int = 13,
    ) -> None:
        self.movesets_json_dir = Path(movesets_json_dir)
        self.random_seed = int(random_seed)

    def _find_latest_month_dir(self) -> Path:
        if not self.movesets_json_dir.exists():
            raise FileNotFoundError(str(self.movesets_json_dir))
        month_dirs = [p for p in self.movesets_json_dir.iterdir() if p.is_dir()]
        if not month_dirs:
            raise FileNotFoundError(f"No month folders in {self.movesets_json_dir}")
        month_dirs.sort(key=lambda p: p.name)
        return month_dirs[-1]

    def _find_highest_elo_file(self, month_dir: Path, tier: str) -> TierSource:
        slug = FORMAT_SLUGS_BY_TIER.get(tier)
        if not slug:
            raise ValueError(f"Unknown tier: {tier}")

        candidates = sorted(month_dir.glob(f"gen9{slug}-*.json"))
        if not candidates:
            raise FileNotFoundError(f"No files for tier {tier} in {month_dir}")

        def elo_key(p: Path) -> int:
            elo = _parse_elo_from_filename(p.name)
            return elo if elo is not None else -1

        candidates.sort(key=elo_key)
        best = candidates[-1]
        return TierSource(month=month_dir.name, tier=tier, elo=_parse_elo_from_filename(best.name), file_path=best)

    def load_tier_highest_elo(self, tier: str) -> tuple[TierSource, dict[str, Any]]:
        month_dir = self._find_latest_month_dir()
        source = self._find_highest_elo_file(month_dir, tier)
        data = json.loads(source.file_path.read_text(encoding="utf-8"))
        return source, data

    def _pokemon_vector(
        self,
        pokemon_entry: dict[str, Any],
        *,
        min_move_pct: float = 0.0,
        min_spread_pct: float = 0.0,
        include_ev_means: bool = True,
        include_natures: bool = True,
    ) -> dict[str, float]:
        v: dict[str, float] = {}

        spreads = pokemon_entry.get("spreads") or []
        spread_total = 0.0

        ev_means = {"hp": 0.0, "atk": 0.0, "def": 0.0, "spa": 0.0, "spd": 0.0, "spe": 0.0}
        nature_weights: dict[str, float] = defaultdict(float)

        for s in spreads:
            pct = float(s.get("usage_pct") or 0.0)
            if pct < min_spread_pct:
                continue
            w = pct / 100.0
            spread_total += w

            if include_ev_means:
                for stat in ev_means.keys():
                    ev_means[stat] += w * (float(s.get(stat) or 0.0) / 252.0)

            if include_natures:
                n = s.get("nature")
                if isinstance(n, str) and n:
                    nature_weights[n] += w

        if spread_total > 0.0:
            if include_ev_means:
                for stat, val in ev_means.items():
                    v[f"ev_mean:{stat}"] = val / spread_total

            if include_natures:
                for nature, w in nature_weights.items():
                    v[f"nature:{nature}"] = w / spread_total

        moves = pokemon_entry.get("moves") or []
        for m in moves:
            pct = float(m.get("usage_pct") or 0.0)
            if pct < min_move_pct:
                continue
            name = m.get("move_name")
            if not isinstance(name, str) or not name:
                continue
            if name.strip().lower() == "other":
                continue
            v[f"move:{name}"] = pct / 100.0

        return _normalize(v)

    def _build_vectors(
        self,
        tier_json: dict[str, Any],
        *,
        min_move_pct: float = 0.0,
        min_spread_pct: float = 0.0,
    ) -> dict[str, dict[str, float]]:
        vectors: dict[str, dict[str, float]] = {}
        for name, entry in tier_json.items():
            if name == "__meta__":
                continue
            if not isinstance(entry, dict):
                continue
            vec = self._pokemon_vector(entry, min_move_pct=min_move_pct, min_spread_pct=min_spread_pct)
            if vec:
                vectors[name] = vec
        return vectors

    def _spherical_kmeans(
        self,
        vectors: dict[str, dict[str, float]],
        *,
        k: int,
        max_iters: int = 40,
    ) -> tuple[dict[int, list[str]], dict[int, dict[str, float]], dict[str, int]]:
        if k <= 0:
            raise ValueError("k must be > 0")
        names = list(vectors.keys())
        if len(names) < k:
            raise ValueError(f"Not enough pokemon to cluster: {len(names)} < k={k}")

        rng = random.Random(self.random_seed)
        init_names = rng.sample(names, k)
        centroids: dict[int, dict[str, float]] = {i: dict(vectors[n]) for i, n in enumerate(init_names)}

        assignment: dict[str, int] = {}

        for _ in range(max_iters):
            changed = 0
            clusters: dict[int, list[str]] = {i: [] for i in range(k)}

            for n in names:
                v = vectors[n]
                best_c = 0
                best_sim = -1e9
                for c in range(k):
                    sim = _dot(v, centroids[c])
                    if sim > best_sim:
                        best_sim = sim
                        best_c = c

                prev = assignment.get(n)
                if prev is None or prev != best_c:
                    changed += 1
                    assignment[n] = best_c
                clusters[best_c].append(n)

            if changed == 0:
                return clusters, centroids, assignment

            new_centroids: dict[int, dict[str, float]] = {}
            for c in range(k):
                members = clusters[c]
                if not members:
                    pick = rng.choice(names)
                    new_centroids[c] = dict(vectors[pick])
                    continue
                agg: dict[str, float] = defaultdict(float)
                for n in members:
                    for f, w in vectors[n].items():
                        agg[f] += w
                new_centroids[c] = _normalize(dict(agg))

            centroids = new_centroids

        clusters: dict[int, list[str]] = {i: [] for i in range(k)}
        for n, c in assignment.items():
            clusters[c].append(n)
        return clusters, centroids, assignment

    def _soft_memberships(
        self,
        vectors: dict[str, dict[str, float]],
        centroids: dict[int, dict[str, float]],
        *,
        top_n: int = 2,
    ) -> dict[str, list[tuple[int, float]]]:
        out: dict[str, list[tuple[int, float]]] = {}
        for name, v in vectors.items():
            sims = [(c, _dot(v, centroids[c])) for c in centroids.keys()]
            sims.sort(key=lambda x: x[1], reverse=True)
            out[name] = sims[:top_n]
        return out

    def _cluster_feature_summary(
        self,
        clusters: dict[int, list[str]],
        vectors: dict[str, dict[str, float]],
        *,
        top_n: int = 15,
    ) -> dict[int, dict[str, list[tuple[str, float]]]]:
        out: dict[int, dict[str, list[tuple[str, float]]]] = {}
        for cid, members in clusters.items():
            if not members:
                out[cid] = {"moves": [], "natures": [], "ev_means": []}
                continue

            agg: dict[str, float] = defaultdict(float)
            for n in members:
                for f, w in vectors[n].items():
                    agg[f] += w

            moves = [(f[5:], w) for f, w in agg.items() if f.startswith("move:")]
            natures = [(f[7:], w) for f, w in agg.items() if f.startswith("nature:")]
            evs = [(f[8:], w) for f, w in agg.items() if f.startswith("ev_mean:")]

            moves.sort(key=lambda x: x[1], reverse=True)
            natures.sort(key=lambda x: x[1], reverse=True)
            evs.sort(key=lambda x: x[1], reverse=True)

            out[cid] = {
                "moves": moves[:top_n],
                "natures": natures[:top_n],
                "ev_means": evs[:6],
            }
        return out

    def cluster_tier(
        self,
        tier: str,
        *,
        k: int = 8,
        max_iters: int = 40,
        min_move_pct: float = 0.0,
        min_spread_pct: float = 0.0,
        soft_top_n: int = 2,
    ) -> ClusterResult:
        source, tier_json = self.load_tier_highest_elo(tier)
        vectors = self._build_vectors(tier_json, min_move_pct=min_move_pct, min_spread_pct=min_spread_pct)

        clusters, centroids, _ = self._spherical_kmeans(vectors, k=k, max_iters=max_iters)
        soft = self._soft_memberships(vectors, centroids, top_n=soft_top_n)
        summaries = self._cluster_feature_summary(clusters, vectors)

        for cid in clusters.keys():
            clusters[cid].sort(key=lambda n: soft[n][0][1] if soft.get(n) else 0.0, reverse=True)

        return ClusterResult(
            source=source,
            k=k,
            clusters=clusters,
            soft_memberships=soft,
            cluster_top_features=summaries,
        )


def _result_to_export_dict(result: ClusterResult) -> dict[str, Any]:
    return {
        "source": {
            "month": result.source.month,
            "tier": result.source.tier,
            "elo": result.source.elo,
            "file_path": str(result.source.file_path),
        },
        "k": result.k,
        "clusters": {str(cid): members for cid, members in result.clusters.items()},
        "cluster_top_features": {
            str(cid): summary for cid, summary in result.cluster_top_features.items()
        },
        "soft_memberships": {
            name: [(int(cid), float(score)) for cid, score in pairs]
            for name, pairs in result.soft_memberships.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Cluster Pokemon roles from movesets_json for a tier")
    parser.add_argument("--tier", required=True, help="Tier name, e.g. OU, UU, RU")
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--movesets-json-dir", default=str(Path(__file__).resolve().parent / "movesets_json"))
    parser.add_argument("--min-move-pct", type=float, default=0.0)
    parser.add_argument("--min-spread-pct", type=float, default=0.0)
    parser.add_argument("--soft-top-n", type=int, default=2)
    parser.add_argument("--top-members", type=int, default=25)
    parser.add_argument("--export", default="", help="Optional path to write full results as JSON")
    args = parser.parse_args()

    clusterer = TierRoleClusterer(movesets_json_dir=Path(args.movesets_json_dir))
    result = clusterer.cluster_tier(
        args.tier,
        k=int(args.k),
        min_move_pct=float(args.min_move_pct),
        min_spread_pct=float(args.min_spread_pct),
        soft_top_n=int(args.soft_top_n),
    )

    print(
        f"Source: month={result.source.month} tier={result.source.tier} elo={result.source.elo} file={result.source.file_path.name}"
    )
    for cid in sorted(result.clusters.keys()):
        members = result.clusters[cid]
        print(f"\n=== Cluster {cid} ({len(members)} pokemon) ===")
        summary = result.cluster_top_features.get(cid, {})
        print("Top moves:", summary.get("moves", [])[:10])
        print("Top natures:", summary.get("natures", [])[:10])
        print("EV signals:", summary.get("ev_means", []))
        print("Members:", members[: int(args.top_members)])

    if args.export:
        export_path = Path(args.export)
        export_path.write_text(json.dumps(_result_to_export_dict(result), indent=2), encoding="utf-8")
        print(f"\nWrote: {export_path}")


if __name__ == "__main__":
    main()
