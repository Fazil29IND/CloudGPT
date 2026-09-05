"""
Hierarchical Navigable Small World (HNSW) Vector Index for CloudGPT.

Implements multi-layer proximity graphs for sub-millisecond approximate nearest neighbor
(ANN) search over cloud documentation, architecture playbooks, and service chunks.
"""

from __future__ import annotations

import heapq
import json
import logging
import math
import random
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


def _l2_normalize(vec: list[float] | np.ndarray) -> np.ndarray:
    """Normalize vector to unit length for exact cosine similarity via dot product."""
    arr = np.asarray(vec, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm == 0.0 or abs(norm - 1.0) < 1e-6:
        return arr
    return arr / norm


class HNSWIndex:
    """In-memory and persisted Hierarchical Navigable Small World (HNSW) vector index."""

    def __init__(
        self,
        dimension: int = 384,
        m: int = 16,
        ef_construction: int = 64,
        ef_search: int = 32,
        seed: int = 42,
    ) -> None:
        self.dimension = dimension
        self.m = m
        self.m0 = 2 * m
        self.ef_construction = ef_construction
        self.ef_search = ef_search
        self.ml = 1.0 / math.log(m) if m > 1 else 1.0
        self.rng = random.Random(seed)

        # Graph storage
        self.entry_point: str | None = None
        self.max_level: int = -1
        # levels: list of dicts mapping node_id -> set of neighbor node_ids
        self.graphs: list[dict[str, set[str]]] = []
        # node metadata and vectors
        self.nodes: dict[str, dict[str, Any]] = {}

    def _random_level(self) -> int:
        """Assign random level with exponentially decaying probability."""
        r = self.rng.random()
        if r == 0.0:
            r = 1e-9
        lvl = int(math.floor(-math.log(r) * self.ml))
        return min(lvl, 16)

    def _distance(self, vec_a: np.ndarray, vec_b: np.ndarray) -> float:
        """Cosine distance: 1.0 - (vec_a . vec_b) on unit vectors."""
        sim = float(np.dot(vec_a, vec_b))
        return 1.0 - max(-1.0, min(1.0, sim))

    def _similarity(self, vec_a: np.ndarray, vec_b: np.ndarray) -> float:
        """Cosine similarity: (vec_a . vec_b) on unit vectors."""
        sim = float(np.dot(vec_a, vec_b))
        return max(-1.0, min(1.0, sim))

    def add_item(
        self,
        node_id: str,
        vector: list[float] | np.ndarray,
        text: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insert a vector item into the multi-layer HNSW graph."""
        vec = _l2_normalize(vector)
        if len(vec) != self.dimension:
            # Dynamically adjust dimension if index is initialized empty
            if len(self.nodes) == 0:
                self.dimension = len(vec)
            else:
                logger.warning(
                    "Dimension mismatch: expected %d, got %d for %s",
                    self.dimension,
                    len(vec),
                    node_id,
                )
                if len(vec) < self.dimension:
                    padded = np.zeros(self.dimension, dtype=np.float32)
                    padded[: len(vec)] = vec
                    vec = padded
                else:
                    vec = vec[: self.dimension]
                    vec = _l2_normalize(vec)

        level = self._random_level()
        while len(self.graphs) <= level:
            self.graphs.append({})

        self.nodes[node_id] = {
            "id": node_id,
            "vector": vec,
            "text": text,
            "metadata": metadata or {},
            "level": level,
        }

        # Initialize neighbor sets across node's levels
        for lvl in range(level + 1):
            self.graphs[lvl][node_id] = set()

        if self.entry_point is None:
            self.entry_point = node_id
            self.max_level = level
            return

        curr_ep = self.entry_point
        curr_dist = self._distance(vec, self.nodes[curr_ep]["vector"])

        # 1. Greedy top-down search to entry level
        for lvl in range(self.max_level, level, -1):
            changed = True
            while changed:
                changed = False
                neighbors = self.graphs[lvl].get(curr_ep, set())
                for neighbor in neighbors:
                    if neighbor not in self.nodes:
                        continue
                    d = self._distance(vec, self.nodes[neighbor]["vector"])
                    if d < curr_dist:
                        curr_dist = d
                        curr_ep = neighbor
                        changed = True

        # 2. Beam search candidate exploration and connection from min(max_level, level) down to 0
        search_level = min(self.max_level, level)
        for lvl in range(search_level, -1, -1):
            candidates = self._search_layer(vec, curr_ep, self.ef_construction, lvl)
            m_max = self.m0 if lvl == 0 else self.m
            neighbors = self._select_neighbors(vec, candidates, m_max)

            # Establish bidirectional links
            for n_id in neighbors:
                self.graphs[lvl][node_id].add(n_id)
                self.graphs[lvl].setdefault(n_id, set()).add(node_id)
                # Prune neighbor if exceeding m_max
                if len(self.graphs[lvl][n_id]) > m_max:
                    n_candidates = [
                        (self._distance(self.nodes[n_id]["vector"], self.nodes[cand]["vector"]), cand)
                        for cand in self.graphs[lvl][n_id]
                        if cand in self.nodes
                    ]
                    pruned = self._select_neighbors(self.nodes[n_id]["vector"], n_candidates, m_max)
                    self.graphs[lvl][n_id] = set(pruned)

            if candidates:
                curr_ep = candidates[0][1]

        if level > self.max_level:
            self.max_level = level
            self.entry_point = node_id

    def _search_layer(
        self,
        query_vec: np.ndarray,
        entry_point: str,
        ef: int,
        level: int,
    ) -> list[tuple[float, str]]:
        """Beam search exploration within a single layer graph."""
        v = {entry_point}
        ep_dist = self._distance(query_vec, self.nodes[entry_point]["vector"])
        # min-heap for candidate exploration: (dist, node_id)
        c = [(ep_dist, entry_point)]
        # max-heap for tracking best ef results: (-dist, node_id)
        w = [(-ep_dist, entry_point)]

        while c:
            cand_dist, cand_id = heapq.heappop(c)
            furthest_dist = -w[0][0]
            if cand_dist > furthest_dist:
                break

            neighbors = self.graphs[level].get(cand_id, set())
            for neighbor in neighbors:
                if neighbor not in v and neighbor in self.nodes:
                    v.add(neighbor)
                    n_dist = self._distance(query_vec, self.nodes[neighbor]["vector"])
                    furthest_dist = -w[0][0]

                    if n_dist < furthest_dist or len(w) < ef:
                        heapq.heappush(c, (n_dist, neighbor))
                        heapq.heappush(w, (-n_dist, neighbor))
                        if len(w) > ef:
                            heapq.heappop(w)

        # Return sorted list of (dist, node_id)
        return sorted([(-item[0], item[1]) for item in w], key=lambda x: x[0])

    def _select_neighbors(
        self,
        query_vec: np.ndarray,
        candidates: list[tuple[float, str]],
        m_max: int,
    ) -> list[str]:
        """Simple greedy neighbor selection (closest m_max candidates)."""
        sorted_cands = sorted(candidates, key=lambda x: x[0])
        return [c[1] for c in sorted_cands[:m_max]]

    def search(
        self,
        query_vector: list[float] | np.ndarray,
        top_k: int = 10,
        ef: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[tuple[str, float, str, dict[str, Any]]]:
        """Search top-k approximate nearest neighbors with cosine similarity and metadata filters.

        Returns:
            list of tuples: (chunk_id, similarity_score, text, metadata)
        """
        if not self.nodes or self.entry_point is None:
            return []

        q_vec = _l2_normalize(query_vector)
        if len(q_vec) != self.dimension:
            if len(q_vec) < self.dimension:
                padded = np.zeros(self.dimension, dtype=np.float32)
                padded[: len(q_vec)] = q_vec
                q_vec = padded
            else:
                q_vec = q_vec[: self.dimension]
                q_vec = _l2_normalize(q_vec)

        ef_val = max(ef or self.ef_search, top_k * 2)
        curr_ep = self.entry_point
        curr_dist = self._distance(q_vec, self.nodes[curr_ep]["vector"])

        # Greedily navigate down to layer 1
        for lvl in range(self.max_level, 0, -1):
            changed = True
            while changed:
                changed = False
                neighbors = self.graphs[lvl].get(curr_ep, set())
                for neighbor in neighbors:
                    if neighbor not in self.nodes:
                        continue
                    d = self._distance(q_vec, self.nodes[neighbor]["vector"])
                    if d < curr_dist:
                        curr_dist = d
                        curr_ep = neighbor
                        changed = True

        # Beam search at layer 0
        candidates = self._search_layer(q_vec, curr_ep, ef_val, 0)

        results: list[tuple[str, float, str, dict[str, Any]]] = []
        for dist, node_id in candidates:
            node = self.nodes.get(node_id)
            if not node:
                continue

            # Check metadata filters
            meta = node.get("metadata", {})
            if filters and not self._matches_filter(meta, filters):
                continue

            similarity = max(0.0, min(1.0, 1.0 - dist))
            results.append((node_id, similarity, node.get("text", ""), meta))

            if len(results) >= top_k:
                break

        return results

    def _matches_filter(self, meta: dict[str, Any], filters: dict[str, Any]) -> bool:
        """Check if node metadata satisfies search filter conditions."""
        from router.query_router import canonical_provider

        for fk, fv in filters.items():
            if fv is None:
                continue
            if fk == "provider":
                node_p = (meta.get("provider") or "").lower()
                norm_node = canonical_provider(node_p)
                if norm_node in ("multi-cloud", "cross-cloud", "all"):
                    continue
                if isinstance(fv, list):
                    norm_targets = {canonical_provider(p) for p in fv}
                else:
                    norm_targets = {canonical_provider(str(fv))}
                if norm_node not in norm_targets and node_p not in norm_targets:
                    return False
            else:
                if meta.get(fk) != fv:
                    return False
        return True

    def save(self, file_path: str | Path) -> None:
        """Serialize HNSW graph and node vectors to a JSON bundle."""
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        serialized_nodes = {}
        for nid, n in self.nodes.items():
            serialized_nodes[nid] = {
                "id": n["id"],
                "vector": n["vector"].tolist(),
                "text": n["text"],
                "metadata": n["metadata"],
                "level": n["level"],
            }

        serialized_graphs = []
        for g in self.graphs:
            serialized_graphs.append({k: list(v) for k, v in g.items()})

        data = {
            "dimension": self.dimension,
            "m": self.m,
            "m0": self.m0,
            "ef_construction": self.ef_construction,
            "ef_search": self.ef_search,
            "max_level": self.max_level,
            "entry_point": self.entry_point,
            "graphs": serialized_graphs,
            "nodes": serialized_nodes,
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        logger.info("Saved HNSW index (%d nodes) to %s", len(self.nodes), path)

    @classmethod
    def load(cls, file_path: str | Path) -> HNSWIndex:
        """Deserialize HNSW graph from file."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"HNSW index file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        idx = cls(
            dimension=data.get("dimension", 384),
            m=data.get("m", 16),
            ef_construction=data.get("ef_construction", 64),
            ef_search=data.get("ef_search", 32),
        )
        idx.m0 = data.get("m0", 32)
        idx.max_level = data.get("max_level", -1)
        idx.entry_point = data.get("entry_point")

        idx.graphs = []
        for g_dict in data.get("graphs", []):
            idx.graphs.append({k: set(v) for k, v in g_dict.items()})

        idx.nodes = {}
        for nid, n in data.get("nodes", {}).items():
            idx.nodes[nid] = {
                "id": n["id"],
                "vector": np.asarray(n["vector"], dtype=np.float32),
                "text": n.get("text", ""),
                "metadata": n.get("metadata", {}),
                "level": n.get("level", 0),
            }

        logger.info("Loaded HNSW index with %d nodes from %s", len(idx.nodes), path)
        return idx
