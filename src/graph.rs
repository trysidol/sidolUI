//! Reactive dependency graph — signals, edges, and dirty propagation.
//!
//! A directed graph where nodes are signals and edges are "depends-on"
//! relationships. When signal A is written, every signal that transitively
//! depends on A is marked dirty. This is the same mechanism as SolidJS,
//! MobX, Vue, Svelte — fine-grained reactivity.
//!
//! No signal values live here. The graph tracks only existence (node set),
//! topology (edge set), and worklist (dirty set). Values are owned by Python
//! in `instance._state_values` — duplicating them in Rust would mean
//! maintaining a second truth that can drift from the original.
//!
//! # Iterative stack walk (not recursive DFS)
//!
//! `mark_dirty` uses an explicit Vec stack because:
//! 1. Deep widget trees (button in a row in a column in a scroll...) can
//!    overflow the debug-mode stack guard with recursion.
//! 2. The `dirty.insert` check doubles as a cycle guard — revisiting an
//!    already-dirty node short-circuits. Recursive DFS would need a separate
//!    "visiting" set.
//!
//! # Bidirectional edge storage
//!
//! `dependents` (forward) is all that's needed for dirty propagation.
//! `sources` (backward) exists only for `clear_observer` — removing all
//! incoming edges for a re-rendering component so stale conditional
//! subscriptions are pruned. Without `sources`, we'd scan *every* node.
//! This is the stale-conditional-subscription bug, the #1 mistake in naive
//! reactive implementations.

use std::collections::{HashMap, HashSet};

// SignalId — newtype prevents mixing with other integer IDs in the engine.

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct SignalId(usize);

impl SignalId {
    pub fn from_raw(raw: usize) -> Self {
        Self(raw)
    }

    pub fn raw(&self) -> usize {
        self.0
    }
}

// SignalNode — a single vertex in the dependency graph.

#[derive(Debug, Default)]
struct SignalNode {
    /// Forward edge: signals dirtied when this signal changes.
    dependents: HashSet<SignalId>,
    /// Backward edge: signals this node reads from. Used by clear_observer
    /// to efficiently tear down all subscriptions for a re-rendering component.
    sources: HashSet<SignalId>,
}

#[derive(Debug, Default)]
pub struct Graph {
    nodes: HashMap<SignalId, SignalNode>,
    dirty: HashSet<SignalId>,
    next_id: usize,
}

impl Graph {
    pub fn new() -> Self {
        Self::default()
    }

    /// Allocate a new signal with no dependencies, return its ID.
    pub fn create_signal(&mut self) -> SignalId {
        let id = SignalId(self.next_id);
        self.next_id += 1;
        self.nodes.insert(id, SignalNode::default());
        id
    }

    pub fn contains_signal(&self, id: SignalId) -> bool {
        self.nodes.contains_key(&id)
    }

    /// Declare that `dependent` should be dirtied when `source` changes.
    /// Edge is stored bidirectionally — forward for propagation, backward
    /// for teardown. Idempotent on repeat calls with the same pair.
    pub fn add_dependency(&mut self, source: SignalId, dependent: SignalId) {
        if !self.nodes.contains_key(&source) || !self.nodes.contains_key(&dependent) {
            return;
        }
        self.nodes
            .get_mut(&source)
            .unwrap()
            .dependents
            .insert(dependent);
        self.nodes
            .get_mut(&dependent)
            .unwrap()
            .sources
            .insert(source);
    }

    /// Remove all subscription edges for `observer`. After this, `observer`
    /// has no incoming dependencies — a clean slate for re-subscription
    /// during re-render.
    ///
    /// Without this, add_dependency only ever adds edges, never removes them.
    /// A component that reads self.a on frame 1 and self.b on frame 2 would
    /// keep both edges, re-rendering when either changes — a correctness bug
    /// (spurious re-renders, possible infinite loops).
    pub fn clear_observer(&mut self, observer: SignalId) {
        // Collect sources to Vec first to avoid simultaneous borrow of
        // self.nodes (can't hold &observer.sources and &mut source.dependents
        // at the same time across a HashMap).
        let sources: Vec<SignalId> = self
            .nodes
            .get(&observer)
            .map(|n| n.sources.iter().copied().collect())
            .unwrap_or_default();

        for source in &sources {
            if let Some(source_node) = self.nodes.get_mut(source) {
                source_node.dependents.remove(&observer);
            }
        }

        if let Some(node) = self.nodes.get_mut(&observer) {
            node.sources.clear();
        }
    }

    /// Remove a signal and all edges touching it.
    pub fn remove_signal(&mut self, id: SignalId) {
        let Some(node) = self.nodes.remove(&id) else {
            return;
        };
        for source in node.sources {
            if let Some(source_node) = self.nodes.get_mut(&source) {
                source_node.dependents.remove(&id);
            }
        }
        for dependent in node.dependents {
            if let Some(dependent_node) = self.nodes.get_mut(&dependent) {
                dependent_node.sources.remove(&id);
            }
        }
        self.dirty.remove(&id);
    }

    /// Mark `id` dirty and propagate to all transitive dependents.
    /// Iterative DFS (not recursion) — see module doc for why.
    ///
    /// Uses a per-call `visited` set for cycle detection and a
    /// persistent `dirty` set for the worklist. These are separate:
    /// `visited` prevents infinite re-visits within one propagation
    /// call; `dirty` collects all dirty signals across multiple calls.
    ///
    /// Previously `dirty.insert` served both roles, which meant a
    /// signal that was already dirty (from an earlier propagation)
    /// would skip re-propagating to dependents — including dependents
    /// that were added AFTER the first mark_dirty call. This is a
    /// correctness bug (👻 stale-subscription variant).
    pub fn mark_dirty(&mut self, id: SignalId) {
        let mut stack = vec![id];
        let mut visited = HashSet::new();
        while let Some(current) = stack.pop() {
            if !visited.insert(current) {
                continue;
            }
            self.dirty.insert(current);
            if let Some(node) = self.nodes.get(&current) {
                stack.extend(node.dependents.iter().copied());
            }
        }
    }

    /// Snapshot the current dirty set and clear it atomically.
    /// This is the primary read-out path; callers MUST NOT inspect
    /// `dirty_ids()` and then separately call `clear_dirty()` — if a
    /// concurrent (or re-entrant) write inserts a new dirty signal
    /// between the two calls, that signal is silently dropped.
    ///
    /// Using `drain_dirty()` instead of {dirty_ids, clear_dirty}
    /// is both safer and one FFI call cheaper.
    pub fn drain_dirty(&mut self) -> Vec<SignalId> {
        let ids: Vec<SignalId> = self.dirty.iter().copied().collect();
        self.dirty.clear();
        ids
    }

    pub fn dirty_ids(&self) -> Vec<SignalId> {
        self.dirty.iter().copied().collect()
    }

    /// Clear the dirty set. Call AFTER consuming (re-rendering) the dirty
    /// signals, never before.
    pub fn clear_dirty(&mut self) {
        self.dirty.clear();
    }

    /// Wipe the graph — all nodes, edges, and dirty state. Test isolation
    /// only. IDs are deliberately not reused so delayed component cleanup
    /// cannot remove a newer component's signal after a reset.
    pub fn reset(&mut self) {
        self.nodes.clear();
        self.dirty.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dirty_propagates_to_dependents() {
        let mut graph = Graph::new();
        let a = graph.create_signal();
        let b = graph.create_signal();
        graph.add_dependency(a, b);

        graph.mark_dirty(a);

        let dirty = graph.dirty_ids();
        assert!(dirty.contains(&a));
        assert!(dirty.contains(&b));
    }

    #[test]
    fn unrelated_signals_stay_clean() {
        let mut graph = Graph::new();
        let a = graph.create_signal();
        let unrelated = graph.create_signal();

        graph.mark_dirty(a);

        assert!(!graph.dirty_ids().contains(&unrelated));
    }

    #[test]
    fn self_referential_dependency_terminates() {
        let mut graph = Graph::new();
        let a = graph.create_signal();
        graph.add_dependency(a, a);

        graph.mark_dirty(a);

        let dirty = graph.dirty_ids();
        assert_eq!(dirty.len(), 1);
        assert!(dirty.contains(&a));
    }

    #[test]
    fn clear_observer_removes_stale_edges() {
        let mut graph = Graph::new();
        let state_a = graph.create_signal();
        let state_b = graph.create_signal();
        let view = graph.create_signal();

        graph.add_dependency(state_a, view);
        graph.clear_observer(view);
        graph.add_dependency(state_b, view);

        graph.mark_dirty(state_a);
        assert!(!graph.dirty_ids().contains(&view));

        graph.clear_dirty();
        graph.mark_dirty(state_b);
        assert!(graph.dirty_ids().contains(&view));
    }

    #[test]
    fn reset_clears_all_state() {
        let mut graph = Graph::new();
        let a = graph.create_signal();
        graph.mark_dirty(a);

        graph.reset();

        assert!(graph.dirty_ids().is_empty());
        assert_eq!(graph.nodes.len(), 0);
    }

    #[test]
    fn mark_dirty_repropagates_to_new_dependents_without_clear() {
        // Repro for the conflated cycle/dedup guard bug.
        // If signal A is already in the persistent dirty set and a
        // new dependent C is wired up, a subsequent mark_dirty(A)
        // must walk A's dependents again — and must pick up C.
        let mut graph = Graph::new();

        let a = graph.create_signal();
        let b = graph.create_signal();
        graph.add_dependency(a, b);
        graph.mark_dirty(a);

        // The dirty set still holds {A, B}. WITHOUT clearing dirty,
        // wire up a brand-new dependent.
        let c = graph.create_signal();
        graph.add_dependency(a, c);

        // Second propagation — C must be picked up even though A
        // is already in the persistent dirty set.
        graph.mark_dirty(a);

        let dirty = graph.dirty_ids();
        assert!(dirty.contains(&c), "new dependent C was never marked dirty");
        // B was also re-dirtied (harmless — idempotent).
        assert!(dirty.contains(&b));
    }

    #[test]
    fn drain_dirty_returns_snapshot_and_clears() {
        let mut graph = Graph::new();
        let a = graph.create_signal();
        graph.mark_dirty(a);

        let ids = graph.drain_dirty();
        assert!(ids.contains(&a));
        assert!(graph.dirty_ids().is_empty());
    }
}
