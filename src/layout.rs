//! Layout engine — maps the declarative Node tree into taffy flexbox positions.

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::*;
use taffy::prelude::*;

/// Compute layout for a Node tree within a given viewport size.
pub fn compute_layout(py: Python<'_>, root: &Bound<PyAny>, viewport_w: f32, viewport_h: f32) -> PyResult<Py<PyAny>> {
    let mut tree = TaffyTree::new();
    let mut nodes: Vec<NodeEntry> = Vec::new();

    let root_id = build_node(root, &mut tree, &mut nodes, 0)?;

    let viewport = Size {
        width: AvailableSpace::Definite(viewport_w),
        height: AvailableSpace::Definite(viewport_h),
    };
    tree.compute_layout(root_id, viewport)
        .map_err(|e| PyRuntimeError::new_err(format!("taffy layout failed: {e}")))?;

    let results = PyList::empty(py);
    for entry in &nodes {
        let layout = tree
            .layout(entry.taffy_id)
            .map_err(|e| PyRuntimeError::new_err(format!("taffy layout lookup failed: {e}")))?;
        let rect = PyDict::new(py);
        rect.set_item("kind", &entry.kind)?;
        rect.set_item("x", layout.location.x)?;
        rect.set_item("y", layout.location.y)?;
        rect.set_item("w", layout.size.width)?;
        rect.set_item("h", layout.size.height)?;
        rect.set_item("depth", entry.depth)?;
        rect.set_item("text", &entry.text)?;
        results.append(rect)?;
    }

    Ok(results.into())
}

struct NodeEntry {
    taffy_id: NodeId,
    kind: String,
    depth: usize,
    text: String,
}

/// Recursively build taffy nodes from the Python Node tree.
///
/// Returns the taffy NodeId for this node. The node entry (with its
/// pre-computed depth) is inserted into `nodes` at the position recorded
/// *before* children were processed, ensuring pre-order output.
fn build_node(
    node: &Bound<PyAny>,
    tree: &mut TaffyTree,
    nodes: &mut Vec<NodeEntry>,
    depth: usize,
) -> PyResult<NodeId> {
    let kind: String = node.getattr("kind")?.extract()?;

    let props_any = node.getattr("props")?;
    let props = props_any.cast::<PyDict>()?;

    let children_any = node.getattr("children")?;
    let children_tuple = children_any.cast::<PyTuple>()?;

    // Record position before recursing into children. All descendants
    // will occupy nodes[before..]; the parent belongs at `before`.
    let before = nodes.len();
    let mut child_ids: Vec<NodeId> = Vec::new();
    for child in children_tuple.iter() {
        let child_id = build_node(&child, tree, nodes, depth + 1)?;
        child_ids.push(child_id);
    }

    let text = extract_text(&kind, props);
    let taffy_id = create_taffy_node(&kind, props, &child_ids, tree, &text)?;

    // Insert parent at `before` — right before its children's subtree
    // (pre-order: parent before its descendants).
    nodes.insert(before, NodeEntry { taffy_id, kind: kind.clone(), depth, text });

    Ok(taffy_id)
}

fn extract_text(kind: &str, props: &Bound<PyDict>) -> String {
    let key = match kind {
        "text" => "content",
        "button" => "label",
        _ => return String::new(),
    };
    match props.get_item(key) {
        Ok(Some(val)) => val.extract::<String>().unwrap_or_default(),
        _ => String::new(),
    }
}

/// Map a Node kind + props to a taffy Style and create the node in the tree.
fn create_taffy_node(
    kind: &str,
    props: &Bound<PyDict>,
    child_ids: &[NodeId],
    tree: &mut TaffyTree,
    text: &str,
) -> PyResult<NodeId> {
    let spacing: f32 = props
        .get_item("spacing")
        .map(|v| v.and_then(|v| v.extract::<f32>().ok()))
        .unwrap_or(Some(0.0_f32))
        .unwrap_or(0.0_f32);

    match kind {
        "row" => {
            let style = Style {
                display: Display::Flex,
                flex_direction: FlexDirection::Row,
                gap: Size { width: length(spacing), height: length(0.0_f32) },
                ..Default::default()
            };
            tree.new_with_children(style, child_ids)
                .map_err(|e| PyRuntimeError::new_err(format!("taffy: {e}")))
        }
        "column" => {
            let style = Style {
                display: Display::Flex,
                flex_direction: FlexDirection::Column,
                gap: Size { width: length(0.0_f32), height: length(spacing) },
                ..Default::default()
            };
            tree.new_with_children(style, child_ids)
                .map_err(|e| PyRuntimeError::new_err(format!("taffy: {e}")))
        }
        "spacer" => {
            let style = Style {
                flex_grow: 1.0_f32,
                ..Default::default()
            };
            tree.new_leaf(style)
                .map_err(|e| PyRuntimeError::new_err(format!("taffy: {e}")))
        }
        "text" => {
            // Use char count, not byte count — "Café" is 4 chars, not 5 bytes.
            let char_count = text.chars().count().max(1);
            let style = Style {
                size: Size { width: length(char_count as f32), height: length(1.0_f32) },
                ..Default::default()
            };
            tree.new_leaf(style)
                .map_err(|e| PyRuntimeError::new_err(format!("taffy: {e}")))
        }
        "button" => {
            let char_count = text.chars().count();
            let w = (char_count + 4).max(5) as f32;
            let style = Style {
                size: Size { width: length(w), height: length(3.0_f32) },
                ..Default::default()
            };
            tree.new_leaf(style)
                .map_err(|e| PyRuntimeError::new_err(format!("taffy: {e}")))
        }
        _ => {
            tree.new_leaf(Style::default())
                .map_err(|e| PyRuntimeError::new_err(format!("taffy: {e}")))
        }
    }
}
