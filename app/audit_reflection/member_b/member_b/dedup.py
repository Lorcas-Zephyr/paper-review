from typing import Dict, List, Tuple

from .config import DedupConfig
from .models import DedupCluster, DedupItem, DedupResult
from .utils import cosine_sim, normalize_text


class Deduplicator:
    def __init__(self, config: DedupConfig = None):
        self.config = config or DedupConfig()

    def _embed(self, texts: List[str]) -> List[List[float]]:
        # 向量化：优先 Sentence-Transformers，失败则降级为 TF-IDF 或哈希。
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            model = SentenceTransformer("all-MiniLM-L6-v2")
            vectors = model.encode(texts, normalize_embeddings=True)
            return [v.tolist() for v in vectors]
        except Exception:
            pass

        try:
            from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore

            vectorizer = TfidfVectorizer(max_features=2048)
            mat = vectorizer.fit_transform(texts)
            return mat.toarray().tolist()
        except Exception:
            # 简单哈希回退，保证流程可跑通
            vectors = []
            for text in texts:
                vec = [0.0] * 256
                for token in normalize_text(text).split():
                    idx = hash(token) % 256
                    vec[idx] += 1.0
                vectors.append(vec)
            return vectors

    def dedup(self, paper_id: str, items: List[DedupItem]) -> DedupResult:
        if not items:
            return DedupResult(paper_id=paper_id, clusters=[], noise=[])

        texts = [i.text for i in items]
        vectors = self._embed(texts)

        labels = self._dbscan_labels(vectors)
        clusters: Dict[int, List[int]] = {}
        noise: List[DedupItem] = []

        for idx, label in enumerate(labels):
            if label == -1:
                noise.append(items[idx])
                continue
            clusters.setdefault(label, []).append(idx)

        result_clusters: List[DedupCluster] = []
        for cluster_id, indices in clusters.items():
            members = [items[i] for i in indices]
            rep = self._representative_item(indices, vectors, items)
            stats = {
                "size": len(indices),
                "agents": sorted({m.agent for m in members}),
            }
            result_clusters.append(
                DedupCluster(
                    cluster_id=cluster_id,
                    representative_item=rep,
                    members=members,
                    cluster_stats=stats,
                )
            )

        return DedupResult(paper_id=paper_id, clusters=result_clusters, noise=noise)

    def _dbscan_labels(self, vectors: List[List[float]]) -> List[int]:
        try:
            from sklearn.cluster import DBSCAN  # type: ignore

            import numpy as np

            dist_matrix = 1 - (np.dot(vectors, np.array(vectors).T))
            model = DBSCAN(
                eps=self.config.eps,
                min_samples=self.config.min_samples,
                metric="precomputed",
            )
            labels = model.fit_predict(dist_matrix)
            return labels.tolist()
        except Exception:
            return self._fallback_cluster(vectors)

    def _fallback_cluster(self, vectors: List[List[float]]) -> List[int]:
        n = len(vectors)
        labels = [-1] * n
        cluster_id = 0

        for i in range(n):
            if labels[i] != -1:
                continue
            neighbors = self._neighbors(i, vectors)
            if len(neighbors) < self.config.min_samples:
                labels[i] = -1
                continue
            for j in neighbors:
                labels[j] = cluster_id
            cluster_id += 1

        return labels

    def _neighbors(self, idx: int, vectors: List[List[float]]) -> List[int]:
        neighbors = []
        for j, v in enumerate(vectors):
            sim = cosine_sim(vectors[idx], v)
            dist = 1 - sim
            if dist <= self.config.eps:
                neighbors.append(j)
        return neighbors

    def _representative_item(
        self,
        indices: List[int],
        vectors: List[List[float]],
        items: List[DedupItem],
    ) -> DedupItem:
        if len(indices) == 1:
            return items[indices[0]]

        best_idx = indices[0]
        best_score = float("inf")
        for i in indices:
            dists = []
            for j in indices:
                if i == j:
                    continue
                dist = 1 - cosine_sim(vectors[i], vectors[j])
                dists.append(dist)
            score = sum(dists) / max(1, len(dists))
            if score < best_score:
                best_score = score
                best_idx = i

        return items[best_idx]
