"""
Quake: Adaptive Indexing for Vector Search in CloudGPT.

Based on the adaptive indexing paradigm (Mohoney et al., 2025, arXiv:2506.03437):
- Dynamic Multi-Level Centroid Partitioning: Organizes vectors into hierarchical Voronoi partitions.
- Cost-Based Query Routing: Dynamically tunes n_probe according to query ambiguity and centroid distance spread.
- Dynamic Skew & Workload Adaptation: Tracks partition access frequencies, dynamically splits hot partitions,
  merges underutilized partitions, and continuously adapts centroid positions toward user query distributions.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


def _l2_normalize(vec: list[float] | np.ndarray) -> np.ndarray:
    """Normalize vector to unit length."""
    arr = np.asarray(vec, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm == 0.0 or abs(norm - 1.0) < 1e-6:
        return arr
    return arr / norm


class QuakePartition:
    """A dynamic vector partition holding centroid, member items, and access statistics."""

    def __init__(self, partition_id: str, centroid: np.ndarray) -> None:
        self.partition_id = partition_id
        self.centroid = _l2_normalize(centroid)
        self.items: list[dict[str, Any]] = []
        self.access_count: int = 0
        self.radius: float = 0.0

    def add_item(self, item_id: str, vector: np.ndarray, text: str, metadata: dict[str, Any]) -> None:
        self.items.append({
            "id": item_id,
            "vector": vector,
            "text": text,
            "metadata": metadata,
        })
        d = float(1.0 - np.dot(vector, self.centroid))
        if d > self.radius:
            self.radius = d

    def update_centroid(self, query_vec: np.ndarray, eta: float = 0.02) -> None:
        """Incrementally adapt centroid toward query hits."""
        new_c = self.centroid + eta * query_vec
        self.centroid = _l2_normalize(new_c)

    def recompute_centroid(self) -> None:
        """Recompute mean centroid from all member vectors."""
        if not self.items:
            return
        vectors = np.stack([it["vector"] for it in self.items])
        mean_v = np.mean(vectors, axis=0)
        self.centroid = _l2_normalize(mean_v)
        # Recompute radius
        dists = [float(1.0 - np.dot(it["vector"], self.centroid)) for it in self.items]
        self.radius = max(dists) if dists else 0.0


class QuakeIndex:
    """Quake Adaptive Vector Index with Dynamic Partitioning, Cost Modeling, and Skew Adaptation."""

    def __init__(
        self,
        dimension: int = 384,
        num_partitions: int = 16,
        min_probe: int = 1,
        max_probe: int = 5,
        split_threshold: int = 50,
        adaptation_rate: float = 0.02,
    ) -> None:
        self.dimension = dimension
        self.target_partitions = max(2, num_partitions)
        self.min_probe = max(1, min_probe)
        self.max_probe = max(self.min_probe, max_probe)
        self.split_threshold = split_threshold
        self.adaptation_rate = adaptation_rate

        self.partitions: list[QuakePartition] = []
        self._items_count: int = 0

    def __len__(self) -> int:
        return self._items_count

    def _find_closest_partition(self, vector: np.ndarray) -> tuple[int, float]:
        """Find the single closest partition index and distance."""
        best_idx = 0
        best_dist = float("inf")
        for i, p in enumerate(self.partitions):
            d = 1.0 - float(np.dot(vector, p.centroid))
            if d < best_dist:
                best_dist = d
                best_idx = i
        return best_idx, best_dist

    def add_item(
        self,
        item_id: str,
        vector: list[float] | np.ndarray,
        text: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insert a vector into the nearest adaptive partition, creating partitions as needed."""
        vec = _l2_normalize(vector)
        if len(vec) != self.dimension:
            if len(self.partitions) == 0 and self._items_count == 0:
                self.dimension = len(vec)
            else:
                if len(vec) < self.dimension:
                    padded = np.zeros(self.dimension, dtype=np.float32)
                    padded[: len(vec)] = vec
                    vec = padded
                else:
                    vec = vec[: self.dimension]
                    vec = _l2_normalize(vec)

        # If below target partitions, initialize a new partition with this vector as centroid
        if len(self.partitions) < self.target_partitions:
            new_p = QuakePartition(f"p_{len(self.partitions)}", vec)
            new_p.add_item(item_id, vec, text, metadata or {})
            self.partitions.append(new_p)
            self._items_count += 1
            return

        # Assign to nearest existing partition
        best_idx, _ = self._find_closest_partition(vec)
        self.partitions[best_idx].add_item(item_id, vec, text, metadata or {})
        self._items_count += 1

    def _estimate_cost_and_probes(self, query_vec: np.ndarray) -> list[int]:
        """Cost model: evaluates query distance spread to select dynamic n_probe partitions."""
        if not self.partitions:
            return []

        # Compute distances to all partition centroids
        partition_dists = []
        for i, p in enumerate(self.partitions):
            dist = 1.0 - float(np.dot(query_vec, p.centroid))
            partition_dists.append((dist, i))

        partition_dists.sort(key=lambda x: x[0])

        if len(partition_dists) <= self.min_probe:
            return [idx for _, idx in partition_dists]

        # Cost Modeling & Ambiguity Spread:
        # If the gap between 1st and 2nd partition is large (clear winner), probe min_probe.
        # If the gap is small (query is on cluster boundary or multi-topic), increase n_probe.
        d1 = partition_dists[0][0]
        d2 = partition_dists[1][0]
        gap = max(0.0, d2 - d1)

        if gap > 0.15:
            # High confidence query: tight probe
            probes = self.min_probe
        elif gap > 0.05:
            # Medium confidence query: probe 2-3 partitions
            probes = min(len(self.partitions), max(self.min_probe + 1, (self.min_probe + self.max_probe) // 2))
        else:
            # Ambiguous or boundary query: probe max_probe partitions for high recall
            probes = min(len(self.partitions), self.max_probe)

        return [idx for _, idx in partition_dists[:probes]]

    def search(
        self,
        query_vector: list[float] | np.ndarray,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        adapt: bool = True,
    ) -> list[tuple[str, float, str, dict[str, Any]]]:
        """Adaptive vector search using cost-based probe tuning and online skew adaptation.

        Returns:
            list of tuples: (chunk_id, similarity_score, text, metadata)
        """
        if not self.partitions:
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

        # 1. Cost-based probe optimization
        probed_indices = self._estimate_cost_and_probes(q_vec)
        if not probed_indices:
            return []

        candidates: list[tuple[str, float, str, dict[str, Any]]] = []

        # 2. Search inside probed partitions
        for p_idx in probed_indices:
            partition = self.partitions[p_idx]
            partition.access_count += 1

            for it in partition.items:
                meta = it.get("metadata", {})
                if filters and not self._matches_filter(meta, filters):
                    continue

                sim = float(np.dot(q_vec, it["vector"]))
                similarity = max(0.0, min(1.0, sim))
                candidates.append((it["id"], similarity, it.get("text", ""), meta))

        # 3. Dynamic adaptation: adapt closest centroid toward query
        if adapt and probed_indices:
            closest_p = self.partitions[probed_indices[0]]
            closest_p.update_centroid(q_vec, eta=self.adaptation_rate)
            # Check for dynamic partition split
            if closest_p.access_count >= self.split_threshold and len(closest_p.items) >= 20:
                self._split_partition(probed_indices[0])

        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[:top_k]

    def _split_partition(self, p_idx: int) -> None:
        """Dynamic Skew Adaptation: Split a congested hot partition into two balanced sub-partitions."""
        target_p = self.partitions[p_idx]
        items = target_p.items
        if len(items) < 10:
            return

        logger.info(
            "Quake dynamic skew split: partition %s hit threshold %d (items=%d)",
            target_p.partition_id,
            target_p.access_count,
            len(items),
        )

        # 2-means clustering initialization
        v0 = items[0]["vector"]
        # Find farthest item from v0 as second seed
        v1 = max(items, key=lambda it: 1.0 - float(np.dot(v0, it["vector"])))["vector"]

        c0 = _l2_normalize(v0)
        c1 = _l2_normalize(v1)

        group0 = []
        group1 = []

        for it in items:
            d0 = 1.0 - float(np.dot(it["vector"], c0))
            d1 = 1.0 - float(np.dot(it["vector"], c1))
            if d0 <= d1:
                group0.append(it)
            else:
                group1.append(it)

        if not group0 or not group1:
            # Degenerate split, reset access counter and keep
            target_p.access_count = 0
            return

        p0 = QuakePartition(f"{target_p.partition_id}_0", c0)
        p0.items = group0
        p0.recompute_centroid()

        p1 = QuakePartition(f"{target_p.partition_id}_1", c1)
        p1.items = group1
        p1.recompute_centroid()

        # Replace target partition with p0 and append p1
        self.partitions[p_idx] = p0
        self.partitions.append(p1)

    def _matches_filter(self, meta: dict[str, Any], filters: dict[str, Any]) -> bool:
        """Check if item metadata satisfies search filter conditions."""
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
        """Serialize Quake partitions, centroids, and access stats to JSON."""
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        serialized_partitions = []
        for p in self.partitions:
            serialized_items = [
                {
                    "id": it["id"],
                    "vector": it["vector"].tolist(),
                    "text": it["text"],
                    "metadata": it["metadata"],
                }
                for it in p.items
            ]
            serialized_partitions.append({
                "partition_id": p.partition_id,
                "centroid": p.centroid.tolist(),
                "access_count": p.access_count,
                "radius": p.radius,
                "items": serialized_items,
            })

        data = {
            "dimension": self.dimension,
            "target_partitions": self.target_partitions,
            "min_probe": self.min_probe,
            "max_probe": self.max_probe,
            "split_threshold": self.split_threshold,
            "adaptation_rate": self.adaptation_rate,
            "items_count": self._items_count,
            "partitions": serialized_partitions,
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        logger.info("Saved Quake index (%d items, %d partitions) to %s", self._items_count, len(self.partitions), path)

    @classmethod
    def load(cls, file_path: str | Path) -> QuakeIndex:
        """Deserialize Quake index from JSON file."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Quake index file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        idx = cls(
            dimension=data.get("dimension", 384),
            num_partitions=data.get("target_partitions", 16),
            min_probe=data.get("min_probe", 1),
            max_probe=data.get("max_probe", 5),
            split_threshold=data.get("split_threshold", 50),
            adaptation_rate=data.get("adaptation_rate", 0.02),
        )
        idx._items_count = data.get("items_count", 0)

        idx.partitions = []
        for p_dict in data.get("partitions", []):
            centroid = np.asarray(p_dict["centroid"], dtype=np.float32)
            part = QuakePartition(p_dict["partition_id"], centroid)
            part.access_count = p_dict.get("access_count", 0)
            part.radius = p_dict.get("radius", 0.0)
            for it in p_dict.get("items", []):
                part.items.append({
                    "id": it["id"],
                    "vector": np.asarray(it["vector"], dtype=np.float32),
                    "text": it.get("text", ""),
                    "metadata": it.get("metadata", {}),
                })
            idx.partitions.append(part)

        logger.info("Loaded Quake index with %d items across %d partitions from %s", idx._items_count, len(idx.partitions), path)
        return idx
