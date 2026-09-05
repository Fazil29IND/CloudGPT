"""Unit tests for Vector Semantic Cache (core/semantic_cache.py)."""

from core.semantic_cache import SemanticCache, get_semantic_cache


def test_semantic_cache_exact_match():
    cache = SemanticCache(max_entries=10, default_threshold=0.92)
    vec = [1.0, 0.0, 0.0]
    cache.set(vec, "answer_s3_replication")

    match, sim = cache.get([1.0, 0.0, 0.0], threshold=0.92)
    assert match == "answer_s3_replication"
    assert sim is not None
    assert sim >= 0.999


def test_semantic_cache_similarity_above_and_below_threshold():
    cache = SemanticCache(max_entries=10, default_threshold=0.90)
    # Vector pointing along X axis
    vec = [1.0, 0.0, 0.0]
    cache.set(vec, "answer_alb")

    # Highly similar vector (angle ~ 8 degrees, cosine > 0.98)
    near_vec = [0.99, 0.14, 0.0]
    match, sim = cache.get(near_vec, threshold=0.90)
    assert match == "answer_alb"
    assert sim is not None and sim > 0.95

    # Dissimilar vector (orthogonal along Y axis)
    far_vec = [0.0, 1.0, 0.0]
    match_far, sim_far = cache.get(far_vec, threshold=0.90)
    assert match_far is None
    assert sim_far == 0.0


def test_semantic_cache_max_entries_fifo_eviction():
    cache = SemanticCache(max_entries=2, default_threshold=0.90)
    v1 = [1.0, 0.0, 0.0]
    v2 = [0.0, 1.0, 0.0]
    v3 = [0.0, 0.0, 1.0]

    cache.set(v1, "ans1")
    cache.set(v2, "ans2")
    cache.set(v3, "ans3")

    assert len(cache) == 2
    # v1 should have been evicted
    match1, _ = cache.get(v1, threshold=0.95)
    assert match1 is None

    match3, _ = cache.get(v3, threshold=0.95)
    assert match3 == "ans3"


def test_semantic_cache_empty_or_zero_vectors():
    cache = SemanticCache(max_entries=5)
    assert cache.get([], threshold=0.90) == (None, 0.0)
    assert cache.get([0.0, 0.0], threshold=0.90) == (None, 0.0)


def test_get_semantic_cache_singleton():
    s1 = get_semantic_cache()
    s2 = get_semantic_cache()
    assert s1 is s2


def test_semantic_cache_intent_aware_thresholds():
    cache = SemanticCache(max_entries=10, default_threshold=0.92)
    # Target vector
    vec = [1.0, 0.0, 0.0]
    cache.set(vec, "cost_ec2_t4g")

    # Vector with similarity ~0.92 (approx 23 degrees off: cos(23 deg) ~ 0.92)
    # [0.92, 0.39, 0.0] -> norm is sqrt(0.92^2 + 0.39^2) = sqrt(0.8464 + 0.1521) ~ 0.999
    query_vec = [0.92, 0.39, 0.0]

    # For pricing queries, threshold is 0.95 -> should REJECT match to avoid stale/inaccurate pricing
    match_pricing, score = cache.get(query_vec, intent="pricing")
    assert match_pricing is None
    assert score > 0.91

    # For conceptual queries, threshold is 0.90 -> should ACCEPT match
    match_conceptual, score = cache.get(query_vec, intent="conceptual")
    assert match_conceptual == "cost_ec2_t4g"
    assert score > 0.91


def test_generate_semcache_key():
    from core.semantic_cache import generate_semcache_key
    k1 = generate_semcache_key("What is S3 Vectors?", corpus_version="v1", prompt_version="v1")
    k2 = generate_semcache_key("What is S3 Vectors?", corpus_version="v2", prompt_version="v1")
    k3 = generate_semcache_key("What is S3 Vectors?", corpus_version="v1", prompt_version="v2")

    assert k1.startswith("semcache:v1:v1:")
    assert k2.startswith("semcache:v2:v1:")
    assert k3.startswith("semcache:v1:v2:")
    assert k1 != k2
    assert k1 != k3


def test_semantic_cache_troubleshooting_threshold():
    cache = SemanticCache(max_entries=10, default_threshold=0.92)
    vec = [1.0, 0.0, 0.0]
    cache.set(vec, "k8s_crashloopbackoff")

    query_vec = [0.93, 0.36, 0.0]  # ~0.93 similarity
    # Troubleshooting threshold is 0.95 -> should reject 0.93
    match_tb, score = cache.get(query_vec, intent="troubleshooting")
    assert match_tb is None
    assert score > 0.92


