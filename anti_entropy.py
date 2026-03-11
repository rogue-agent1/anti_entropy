#!/usr/bin/env python3
"""Anti-entropy protocol — Merkle tree based replica synchronization.

One file. Zero deps. Does one thing well.

Detects and repairs inconsistencies between replicas using Merkle trees.
Used in Cassandra, DynamoDB, Riak for read-repair and anti-entropy repair.
"""
import hashlib, sys

class MerkleNode:
    __slots__ = ('hash', 'left', 'right', 'lo', 'hi')
    def __init__(self, lo, hi, hash_val=None, left=None, right=None):
        self.lo = lo
        self.hi = hi
        self.hash = hash_val
        self.left = left
        self.right = right

def _hash(data):
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()[:16]

class MerkleTree:
    def __init__(self, data):
        """Build Merkle tree from dict {key: value}."""
        self.data = dict(data)
        keys = sorted(data.keys())
        self.root = self._build(keys, 0, len(keys) - 1) if keys else None

    def _build(self, keys, lo, hi):
        if lo == hi:
            k = keys[lo]
            h = _hash(f"{k}:{self.data[k]}")
            return MerkleNode(k, k, h)
        mid = (lo + hi) // 2
        left = self._build(keys, lo, mid)
        right = self._build(keys, mid + 1, hi)
        h = _hash(left.hash + right.hash)
        return MerkleNode(left.lo, right.hi, h, left, right)

    def root_hash(self):
        return self.root.hash if self.root else None

def find_differences(tree1, tree2):
    """Find keys that differ between two Merkle trees."""
    diffs = []
    _compare(tree1.root, tree2.root, tree1.data, tree2.data, diffs)
    return diffs

def _compare(n1, n2, data1, data2, diffs):
    if n1 is None and n2 is None:
        return
    if n1 is None:
        _collect_keys(n2, diffs, "missing_in_1")
        return
    if n2 is None:
        _collect_keys(n1, diffs, "missing_in_2")
        return
    if n1.hash == n2.hash:
        return  # Subtrees match
    if n1.lo == n1.hi and n2.lo == n2.hi:
        if n1.lo == n2.lo:
            diffs.append(("modified", n1.lo, data1.get(n1.lo), data2.get(n2.lo)))
        else:
            diffs.append(("only_in_1", n1.lo, data1.get(n1.lo), None))
            diffs.append(("only_in_2", n2.lo, None, data2.get(n2.lo)))
        return
    _compare(n1.left if n1.left else None, n2.left if n2.left else None, data1, data2, diffs)
    _compare(n1.right if n1.right else None, n2.right if n2.right else None, data1, data2, diffs)

def _collect_keys(node, diffs, kind):
    if node is None: return
    if node.lo == node.hi:
        diffs.append((kind, node.lo))
        return
    _collect_keys(node.left, diffs, kind)
    _collect_keys(node.right, diffs, kind)

class Replica:
    def __init__(self, name, data=None):
        self.name = name
        self.data = dict(data or {})

    def put(self, key, value):
        self.data[key] = value

    def merkle(self):
        return MerkleTree(self.data)

    def sync_from(self, other):
        """Anti-entropy: sync with another replica."""
        my_tree = self.merkle()
        their_tree = other.merkle()
        if my_tree.root_hash() == their_tree.root_hash():
            return 0  # In sync
        diffs = find_differences(my_tree, their_tree)
        repaired = 0
        for diff in diffs:
            if diff[0] == "modified":
                # Take the other's value (or use timestamp, here simplified)
                self.data[diff[1]] = diff[3]
                repaired += 1
            elif diff[0] == "missing_in_1":
                self.data[diff[1]] = other.data.get(diff[1])
                repaired += 1
        return repaired

def main():
    # Two replicas diverge
    r1 = Replica("node-1")
    r2 = Replica("node-2")
    # Both have same base data
    for i in range(100):
        r1.put(f"key-{i:03d}", f"value-{i}")
        r2.put(f"key-{i:03d}", f"value-{i}")
    # Diverge
    r1.put("key-042", "updated-by-r1")
    r2.put("key-077", "updated-by-r2")
    r2.put("key-099", "updated-by-r2")

    t1 = r1.merkle()
    t2 = r2.merkle()
    print(f"Replica 1 hash: {t1.root_hash()}")
    print(f"Replica 2 hash: {t2.root_hash()}")
    print(f"In sync: {t1.root_hash() == t2.root_hash()}")

    diffs = find_differences(t1, t2)
    print(f"\nDifferences found: {len(diffs)}")
    for d in diffs:
        print(f"  {d}")

    repaired = r1.sync_from(r2)
    print(f"\nRepaired {repaired} keys on replica 1")
    print(f"In sync now: {r1.merkle().root_hash() == r2.merkle().root_hash()}")

if __name__ == "__main__":
    main()
