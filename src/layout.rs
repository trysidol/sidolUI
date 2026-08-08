//! Layout engine — maps a declarative Node tree into taffy flexbox positions.
//!
//! This module is **pure Rust** with zero PyO3 dependency. The conversion
//! from Python Node objects to `LayoutNode` happens one layer up in
//! `lib.rs`. This boundary lets us test all layout logic with `cargo test`.

use taffy::geometry::Point;
use taffy::prelude::*;
use taffy::style::Overflow;
use unicode_width::UnicodeWidthStr;

// ---------------------------------------------------------------------------
// Public types — callers construct LayoutNode trees and get back LayoutEntries
// ---------------------------------------------------------------------------

/// A declarative tree node, mirroring the Python `Node` dataclass.
/// Pure data — no PyO3, no FFI, no lifetimes.
#[derive(Debug, Clone, Default)]
pub struct LayoutNode {
    pub kind: String,
    pub spacing: f32,
    pub min_w: Option<f32>,
    pub min_h: Option<f32>,
    pub max_w: Option<f32>,
    pub max_h: Option<f32>,
    pub padding: f32,
    pub text: String,
    pub fg: String,
    pub bg: String,
    pub variant: String,
    pub disabled: bool,
    pub children: Vec<LayoutNode>,
}

/// One computed layout rect — the output item for one tree node.
#[derive(Debug, Clone)]
pub struct LayoutEntry {
    pub kind: String,
    pub x: f32,
    pub y: f32,
    pub w: f32,
    pub h: f32,
    pub depth: usize,
    pub text: String,
    pub fg: String,
    pub bg: String,
    pub variant: String,
    pub disabled: bool,
}

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------

/// Run the taffy flexbox engine on a `LayoutNode` tree and return a flat
/// list of `LayoutEntry` values in pre-order (parent before children).
pub fn compute_layout(
    root: &LayoutNode,
    viewport_w: f32,
    viewport_h: f32,
) -> Result<Vec<LayoutEntry>, String> {
    if !viewport_w.is_finite() || !viewport_h.is_finite() || viewport_w < 0.0 || viewport_h < 0.0 {
        return Err("viewport dimensions must be finite and non-negative".to_string());
    }
    validate_node(root)?;
    let mut tree = TaffyTree::new();
    // Internal entries carry a taffy NodeId for the position lookup pass.
    let mut entries: Vec<Option<InternalEntry>> = Vec::new();

    let root_id = build_node(root, &mut tree, &mut entries, 0, None)?;

    let viewport = Size {
        width: AvailableSpace::Definite(viewport_w),
        height: AvailableSpace::Definite(viewport_h),
    };
    tree.compute_layout(root_id, viewport)
        .map_err(|e| format!("taffy layout failed: {e}"))?;

    // Second pass: fill in computed positions from taffy.
    let mut results: Vec<LayoutEntry> = Vec::with_capacity(entries.len());
    for entry in entries.into_iter().flatten() {
        let layout = tree
            .layout(entry.taffy_id)
            .map_err(|e| format!("taffy layout lookup failed: {e}"))?;
        let (x, y) = match entry.parent_index {
            Some(parent) => {
                let parent_result = &results[parent];
                (
                    parent_result.x + layout.location.x,
                    parent_result.y + layout.location.y,
                )
            }
            None => (layout.location.x, layout.location.y),
        };
        results.push(LayoutEntry {
            kind: entry.kind,
            x,
            y,
            w: layout.size.width,
            h: layout.size.height,
            depth: entry.depth,
            text: entry.text,
            fg: entry.fg,
            bg: entry.bg,
            variant: entry.variant,
            disabled: entry.disabled,
        });
    }

    Ok(results)
}

fn validate_node(node: &LayoutNode) -> Result<(), String> {
    if !matches!(
        node.kind.as_str(),
        "row" | "column" | "spacer" | "scroll_view" | "text" | "button"
    ) {
        return Err(format!("unsupported node kind: {}", node.kind));
    }
    for (name, value) in [("spacing", node.spacing), ("padding", node.padding)] {
        if !value.is_finite() || value < 0.0 {
            return Err(format!("{name} must be finite and non-negative"));
        }
    }
    for (name, value) in [
        ("min_w", node.min_w),
        ("min_h", node.min_h),
        ("max_w", node.max_w),
        ("max_h", node.max_h),
    ] {
        if let Some(value) = value {
            if !value.is_finite() || value < 0.0 {
                return Err(format!("{name} must be finite and non-negative"));
            }
        }
    }
    if let (Some(min), Some(max)) = (node.min_w, node.max_w) {
        if min > max {
            return Err("min_w cannot exceed max_w".to_string());
        }
    }
    if let (Some(min), Some(max)) = (node.min_h, node.max_h) {
        if min > max {
            return Err("min_h cannot exceed max_h".to_string());
        }
    }
    for child in &node.children {
        validate_node(child)?;
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

struct InternalEntry {
    taffy_id: NodeId,
    parent_index: Option<usize>,
    kind: String,
    depth: usize,
    text: String,
    fg: String,
    bg: String,
    variant: String,
    disabled: bool,
}

/// Recursively build taffy nodes from a `LayoutNode` tree.
/// Returns the taffy `NodeId` for this node.
fn build_node(
    node: &LayoutNode,
    tree: &mut TaffyTree,
    entries: &mut Vec<Option<InternalEntry>>,
    depth: usize,
    parent_index: Option<usize>,
) -> Result<NodeId, String> {
    // Record insertion position before recursing so the parent lands
    // right before its children's subtree (pre-order).
    let before = entries.len();
    entries.push(None);
    let mut child_ids: Vec<NodeId> = Vec::new();
    for child in &node.children {
        let child_id = build_node(child, tree, entries, depth + 1, Some(before))?;
        child_ids.push(child_id);
    }

    let taffy_id = create_taffy_node(node, &child_ids, tree)?;

    entries[before] = Some(InternalEntry {
        taffy_id,
        parent_index,
        kind: node.kind.clone(),
        depth,
        text: node.text.clone(),
        fg: node.fg.clone(),
        bg: node.bg.clone(),
        variant: node.variant.clone(),
        disabled: node.disabled,
    });

    Ok(taffy_id)
}

/// Map a LayoutNode's kind + properties to a taffy Style and create the node.
fn create_taffy_node(
    node: &LayoutNode,
    child_ids: &[NodeId],
    tree: &mut TaffyTree,
) -> Result<NodeId, String> {
    let constraints = Style {
        min_size: Size {
            width: opt_dim(node.min_w),
            height: opt_dim(node.min_h),
        },
        max_size: Size {
            width: opt_dim(node.max_w),
            height: opt_dim(node.max_h),
        },
        padding: Rect {
            left: length(node.padding),
            right: length(node.padding),
            top: length(node.padding),
            bottom: length(node.padding),
        },
        ..Default::default()
    };

    let base = match node.kind.as_str() {
        "row" => Style {
            display: Display::Flex,
            flex_direction: FlexDirection::Row,
            gap: Size {
                width: length(node.spacing),
                height: length(0.0),
            },
            ..constraints
        },
        "column" => Style {
            display: Display::Flex,
            flex_direction: FlexDirection::Column,
            gap: Size {
                width: length(0.0),
                height: length(node.spacing),
            },
            ..constraints
        },
        "scroll_view" => Style {
            display: Display::Flex,
            flex_direction: FlexDirection::Column,
            overflow: Point {
                x: Overflow::Scroll,
                y: Overflow::Scroll,
            },
            ..constraints
        },
        "spacer" => Style {
            flex_grow: 1.0,
            ..constraints
        },
        "text" => {
            let char_count = UnicodeWidthStr::width(node.text.as_str()).max(1);
            Style {
                size: Size {
                    width: length(char_count as f32).into(),
                    height: length(1.0).into(),
                },
                ..constraints
            }
        }
        "button" => {
            let char_count = UnicodeWidthStr::width(node.text.as_str());
            let w = (char_count + 4).max(5) as f32;
            Style {
                size: Size {
                    width: length(w).into(),
                    height: length(3.0).into(),
                },
                ..constraints
            }
        }
        _ => return Err(format!("unsupported node kind: {}", node.kind)),
    };

    if child_ids.is_empty() {
        tree.new_leaf(base).map_err(|e| format!("taffy: {e}"))
    } else {
        tree.new_with_children(base, child_ids)
            .map_err(|e| format!("taffy: {e}"))
    }
}

fn length(v: f32) -> LengthPercentage {
    LengthPercentage::Length(v)
}

fn opt_dim(v: Option<f32>) -> Dimension {
    match v {
        Some(val) => Dimension::Length(val),
        None => Dimension::Auto,
    }
}

// ---------------------------------------------------------------------------
// Pure-Rust unit tests — run with `cargo test`, no Python required
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn text(content: &str) -> LayoutNode {
        LayoutNode {
            kind: "text".into(),
            text: content.into(),
            spacing: 0.0,
            ..Default::default()
        }
    }

    fn button(label: &str) -> LayoutNode {
        LayoutNode {
            kind: "button".into(),
            text: label.into(),
            spacing: 0.0,
            ..Default::default()
        }
    }

    fn row(children: Vec<LayoutNode>, spacing: f32) -> LayoutNode {
        LayoutNode {
            kind: "row".into(),
            spacing,
            children,
            ..Default::default()
        }
    }

    fn column(children: Vec<LayoutNode>, spacing: f32) -> LayoutNode {
        LayoutNode {
            kind: "column".into(),
            spacing,
            children,
            ..Default::default()
        }
    }

    fn spacer() -> LayoutNode {
        LayoutNode {
            kind: "spacer".into(),
            ..Default::default()
        }
    }

    #[test]
    fn layout_simple_text_column() {
        let root = column(vec![text("Hello")], 4.0);
        let entries = compute_layout(&root, 400.0, 300.0).unwrap();

        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].kind, "column");
        assert_eq!(entries[1].kind, "text");
        assert_eq!(entries[0].depth, 0);
        assert_eq!(entries[1].depth, 1);
        assert!(entries[0].w > 0.0);
        assert!(entries[1].w > 0.0);
    }

    #[test]
    fn layout_nested_pre_order() {
        // Tree: Column(Row(Text("A"), Button("B")), Text("C"))
        let root = column(vec![row(vec![text("A"), button("B")], 0.0), text("C")], 0.0);
        let entries = compute_layout(&root, 400.0, 300.0).unwrap();

        assert_eq!(entries.len(), 5);
        let kinds: Vec<&str> = entries.iter().map(|e| e.kind.as_str()).collect();
        assert_eq!(kinds, ["column", "row", "text", "button", "text"]);

        let depths: Vec<usize> = entries.iter().map(|e| e.depth).collect();
        assert_eq!(depths, [0, 1, 2, 2, 1]);
    }

    #[test]
    fn layout_row_spacer_button() {
        let root = row(vec![spacer(), button("OK")], 8.0);
        let entries = compute_layout(&root, 400.0, 300.0).unwrap();

        assert_eq!(entries.len(), 3);
        let kinds: Vec<&str> = entries.iter().map(|e| e.kind.as_str()).collect();
        assert_eq!(kinds, ["row", "spacer", "button"]);
    }

    #[test]
    fn layout_text_sizing_non_ascii() {
        let root = column(vec![text("Café")], 0.0);
        let entries = compute_layout(&root, 400.0, 300.0).unwrap();

        // 4 chars, not 5 bytes
        assert_eq!(entries[1].w, 4.0);
    }

    #[test]
    fn layout_button_sizing() {
        let root = column(vec![button("Click")], 0.0);
        let entries = compute_layout(&root, 400.0, 300.0).unwrap();

        // len("Click") = 5, + 4 padding = 9
        assert_eq!(entries[1].w, 9.0);
        assert_eq!(entries[1].h, 3.0);
    }

    #[test]
    fn layout_empty_children() {
        let root = column(vec![], 0.0);
        let entries = compute_layout(&root, 400.0, 300.0).unwrap();

        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].kind, "column");
    }

    #[test]
    fn layout_unknown_kind_is_rejected() {
        let root = LayoutNode {
            kind: "unknown".into(),
            children: vec![text("child")],
            ..Default::default()
        };
        let error = compute_layout(&root, 400.0, 300.0).unwrap_err();
        assert_eq!(error, "unsupported node kind: unknown");
    }

    #[test]
    fn layout_respects_min_size_constraint() {
        let root = column(vec![text("Hi")], 0.0);
        let root = LayoutNode {
            min_w: Some(100.0),
            min_h: Some(50.0),
            ..root
        };
        let entries = compute_layout(&root, 400.0, 300.0).unwrap();

        assert_eq!(entries[0].kind, "column");
        assert!(
            entries[0].w >= 100.0,
            "width {} should be >= 100",
            entries[0].w
        );
        assert!(
            entries[0].h >= 50.0,
            "height {} should be >= 50",
            entries[0].h
        );
    }

    #[test]
    fn layout_respects_max_size_constraint() {
        let root = LayoutNode {
            kind: "row".into(),
            max_w: Some(50.0),
            max_h: Some(10.0),
            children: vec![text("Hello World")],
            ..Default::default()
        };
        let entries = compute_layout(&root, 400.0, 300.0).unwrap();

        assert!(entries[0].w <= 50.0);
        assert!(entries[0].h <= 10.0);
    }

    #[test]
    fn layout_padding_is_applied() {
        let root = LayoutNode {
            kind: "column".into(),
            padding: 10.0,
            children: vec![text("A")],
            ..Default::default()
        };
        let entries = compute_layout(&root, 400.0, 300.0).unwrap();

        // Taffy applies padding to the container; child position reflects it
        let col = &entries[0];
        let child = &entries[1];
        assert!(
            child.x >= col.x + 10.0,
            "child x {} should be >= parent x {} + padding",
            child.x,
            col.x
        );
        assert!(
            child.y >= col.y + 10.0,
            "child y {} should be >= parent y {} + padding",
            child.y,
            col.y
        );
    }

    #[test]
    fn layout_scroll_view_is_container() {
        let root = LayoutNode {
            kind: "scroll_view".into(),
            children: vec![text("content")],
            ..Default::default()
        };
        let entries = compute_layout(&root, 400.0, 300.0).unwrap();

        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].kind, "scroll_view");
        assert_eq!(entries[1].kind, "text");
        assert_eq!(entries[0].depth, 0);
        assert_eq!(entries[1].depth, 1);
    }

    #[test]
    fn layout_no_constraint_when_none() {
        // Default (no constraints) should still work — regressions-only test
        let root = column(vec![text("OK")], 0.0);
        let entries = compute_layout(&root, 400.0, 300.0).unwrap();

        assert_eq!(entries.len(), 2);
        assert!(entries[0].w > 0.0);
        assert!(entries[0].h > 0.0);
    }
}
