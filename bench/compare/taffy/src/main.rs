//! Lays out a silk-layout CLI workload document with taffy.
//! Usage: taffy-harness WORKLOAD.json ITERATIONS [--dump]
//! Prints {"layoutNs": [...]} and, with --dump, every node's border box in absolute coordinates.
use serde_json::Value;
use std::time::Instant;
use taffy::prelude::*;

#[derive(Clone, Copy)]
struct Fixture {
    min_width: f32,
    max_width: f32,
    height: f32,
}

fn dimension(value: Option<&Value>) -> Dimension {
    match value {
        None => Dimension::auto(),
        Some(Value::String(s)) if s == "auto" => Dimension::auto(),
        Some(Value::Number(n)) => Dimension::length(n.as_f64().unwrap() as f32),
        Some(Value::Object(o)) => Dimension::percent(o["percent"].as_f64().unwrap() as f32),
        Some(other) => panic!("unsupported dimension {other}"),
    }
}

fn min_max(value: Option<&Value>) -> LengthPercentageAuto {
    value.map_or(LengthPercentageAuto::auto(), |v| length_percentage_auto(Some(v)))
}

fn length_percentage(value: Option<&Value>) -> LengthPercentage {
    match value {
        None => LengthPercentage::length(0.0),
        Some(Value::Number(n)) => LengthPercentage::length(n.as_f64().unwrap() as f32),
        Some(Value::Object(o)) => LengthPercentage::percent(o["percent"].as_f64().unwrap() as f32),
        Some(other) => panic!("unsupported length {other}"),
    }
}

fn length_percentage_auto(value: Option<&Value>) -> LengthPercentageAuto {
    match value {
        None => LengthPercentageAuto::length(0.0),
        Some(Value::String(s)) if s == "auto" => LengthPercentageAuto::auto(),
        Some(Value::Number(n)) => LengthPercentageAuto::length(n.as_f64().unwrap() as f32),
        Some(Value::Object(o)) => LengthPercentageAuto::percent(o["percent"].as_f64().unwrap() as f32),
        Some(other) => panic!("unsupported length {other}"),
    }
}

fn edges<T: Copy>(s: &serde_json::Map<String, Value>, name: &str, parse: fn(Option<&Value>) -> T) -> Rect<T> {
    let all = s.get(name);
    let side = |suffix: &str| parse(s.get(&format!("{name}{suffix}")).or(all));
    Rect { left: side("Left"), right: side("Right"), top: side("Top"), bottom: side("Bottom") }
}

fn align(value: Option<&Value>) -> AlignItems {
    value.map_or(AlignItems::NORMAL, |v| match v.as_str().unwrap() {
        "flex-start" => AlignItems::FLEX_START,
        "flex-end" => AlignItems::FLEX_END,
        "start" => AlignItems::START,
        "end" => AlignItems::END,
        "center" => AlignItems::CENTER,
        "baseline" => AlignItems::BASELINE,
        "stretch" => AlignItems::STRETCH,
        other => panic!("unsupported alignItems {other}"),
    })
}

fn justify(value: Option<&Value>) -> JustifyContent {
    value.map_or(JustifyContent::NORMAL, |v| match v.as_str().unwrap() {
        "flex-start" => JustifyContent::FLEX_START,
        "flex-end" => JustifyContent::FLEX_END,
        "start" => JustifyContent::START,
        "end" => JustifyContent::END,
        "center" => JustifyContent::CENTER,
        "space-between" => JustifyContent::SPACE_BETWEEN,
        "space-around" => JustifyContent::SPACE_AROUND,
        "space-evenly" => JustifyContent::SPACE_EVENLY,
        other => panic!("unsupported justifyContent {other}"),
    })
}

fn style(value: &Value) -> Style {
    let s = value.as_object().unwrap();
    let num = |name: &str, default: f32| s.get(name).map(|v| v.as_f64().unwrap() as f32).unwrap_or(default);
    let border = edges(s, "border", length_percentage);
    Style {
        display: match s.get("display").and_then(Value::as_str) {
            Some("flex") => Display::Flex,
            Some("none") => Display::None,
            _ => Display::Block,
        },
        flex_direction: match s.get("flexDirection").and_then(Value::as_str) {
            Some("column") => FlexDirection::Column,
            Some("row-reverse") => FlexDirection::RowReverse,
            Some("column-reverse") => FlexDirection::ColumnReverse,
            _ => FlexDirection::Row,
        },
        flex_wrap: match s.get("flexWrap").and_then(Value::as_str) {
            Some("wrap") => FlexWrap::Wrap,
            Some("wrap-reverse") => FlexWrap::WrapReverse,
            _ => FlexWrap::NoWrap,
        },
        flex_grow: num("flexGrow", 0.0),
        flex_shrink: num("flexShrink", 1.0),
        flex_basis: dimension(s.get("flexBasis")),
        size: Size { width: dimension(s.get("width")), height: dimension(s.get("height")) },
        min_size: Size { width: min_max(s.get("minWidth")), height: min_max(s.get("minHeight")) },
        max_size: Size { width: min_max(s.get("maxWidth")), height: min_max(s.get("maxHeight")) },
        padding: edges(s, "padding", length_percentage),
        margin: edges(s, "margin", length_percentage_auto),
        border,
        gap: Size { width: length_percentage(s.get("columnGap")), height: length_percentage(s.get("rowGap")) },
        align_items: align(s.get("alignItems")),
        justify_content: justify(s.get("justifyContent")),
        ..Default::default()
    }
}

struct Workload {
    tree: TaffyTree<Option<Fixture>>,
    nodes: Vec<NodeId>,
    icb: NodeId,
    viewport: Size<AvailableSpace>,
}

fn build(document: &Value) -> Workload {
    let mut tree: TaffyTree<Option<Fixture>> = TaffyTree::new();
    tree.disable_rounding();
    let mut nodes: Vec<NodeId> = Vec::new();
    let mut children: Vec<Vec<NodeId>> = Vec::new();
    let items = document["nodes"].as_array().unwrap();
    for item in items {
        let fixture = item.get("measure").map(|m| Fixture {
            min_width: m["minWidth"].as_f64().unwrap() as f32,
            max_width: m["maxWidth"].as_f64().unwrap() as f32,
            height: m["height"].as_f64().unwrap() as f32,
        });
        let node = tree.new_leaf_with_context(style(&item["style"]), fixture).unwrap();
        nodes.push(node);
        children.push(Vec::new());
        if let Some(parent) = item["parent"].as_u64() {
            children[parent as usize].push(node);
        }
    }
    for (index, list) in children.iter().enumerate() {
        if !list.is_empty() {
            tree.set_children(nodes[index], list).unwrap();
        }
    }
    // The initial containing block: a fixed-size block box holding the root, as in CSS.
    let viewport = &document["viewport"];
    let (width, height) = (viewport["width"].as_f64().unwrap() as f32, viewport["height"].as_f64().unwrap() as f32);
    let icb = tree
        .new_with_children(
            Style { display: Display::Block, size: Size { width: length(width), height: length(height) }, ..Default::default() },
            &[nodes[0]],
        )
        .unwrap();
    let viewport = Size { width: AvailableSpace::Definite(width), height: AvailableSpace::Definite(height) };
    Workload { tree, nodes, icb, viewport }
}

fn measure(known: Size<Option<f32>>, available: Size<AvailableSpace>, fixture: &Fixture) -> Size<f32> {
    let width = known.width.unwrap_or(match available.width {
        AvailableSpace::MinContent => fixture.min_width,
        AvailableSpace::MaxContent => fixture.max_width,
        AvailableSpace::Definite(w) => w.min(fixture.max_width).max(fixture.min_width),
    });
    Size { width, height: known.height.unwrap_or(fixture.height) }
}

fn layout(workload: &mut Workload) {
    let root = workload.icb;
    workload
        .tree
        .compute_layout_with_measure(root, workload.viewport, |input, _node, context, style| {
            taffy::compute_leaf_layout(input, style, |_, _| 0.0, |known, available| match context {
                Some(Some(fixture)) => measure(known, available, fixture),
                _ => Size::ZERO,
            })
        })
        .unwrap();
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let document: Value = serde_json::from_slice(&std::fs::read(&args[1]).unwrap()).unwrap();
    let iterations: usize = args[2].parse().unwrap();
    let dump = args.iter().any(|a| a == "--dump");
    let mut samples = Vec::with_capacity(iterations);
    let mut last = build(&document);
    layout(&mut last);
    for _ in 0..iterations {
        // A fresh tree per iteration: taffy caches layouts inside the tree, while silk-layout
        // recomputes everything on every call.
        let mut workload = build(&document);
        let start = Instant::now();
        layout(&mut workload);
        samples.push(start.elapsed().as_nanos() as u64);
        last = workload;
    }
    print!("{{\"layoutNs\":{:?}", samples);
    if dump {
        let items = document["nodes"].as_array().unwrap();
        let mut origin: Vec<(f32, f32)> = Vec::with_capacity(items.len());
        print!(",\"boxes\":[");
        for (index, item) in items.iter().enumerate() {
            let l = last.tree.layout(last.nodes[index]).unwrap();
            let (px, py) = item["parent"].as_u64().map(|p| origin[p as usize]).unwrap_or((0.0, 0.0));
            let (x, y) = (px + l.location.x, py + l.location.y);
            origin.push((x, y));
            if index > 0 {
                print!(",");
            }
            print!("[{},{},{},{}]", x, y, l.size.width, l.size.height);
        }
        print!("]");
    }
    println!("}}");
}
