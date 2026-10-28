//! Lays out a silk-layout CLI workload document with taffy.
//! Usage: taffy-harness WORKLOAD.json ITERATIONS [--dump] [--updates CYCLE.json]
//! Prints {"layoutNs": [...]} and, with --dump, every node's border box in absolute coordinates.
//! With --updates, one tree is kept: each iteration applies the next step of the cycle (a new
//! viewport and/or node styles, as in the silk-layout CLI's "updates" field) and times one layout.
//! Grid mapping is intentionally limited to numeric lines/spans, non-repeated tracks
//! (fixed, percent, fr, auto, intrinsic keywords, minmax and fit-content), row/column
//! sparse/dense flow, gaps and item/self alignment. Named placement/areas, repeat and calc are rejected
//! rather than approximated. Fixture wrapping models horizontal text without
//! floats; it does not expose line boxes or baseline data to taffy.
use serde_json::Value;
use std::time::Instant;
use taffy::prelude::*;

#[derive(Clone, Copy)]
struct Fixture {
    min_width: f32,
    max_width: f32,
    height: f32,
    wrap: bool,
    has_line_boxes: bool,
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
        "normal" => AlignItems::NORMAL,
        "flex-start" => AlignItems::FLEX_START,
        "flex-end" => AlignItems::FLEX_END,
        "start" => AlignItems::START,
        "end" => AlignItems::END,
        "center" => AlignItems::CENTER,
        "baseline" => AlignItems::BASELINE,
        "stretch" => AlignItems::STRETCH,
        other => panic!("unsupported item alignment {other}"),
    })
}

fn self_align(value: Option<&Value>) -> Option<AlignSelf> {
    match value {
        None => None,
        Some(Value::String(s)) if s == "auto" => None,
        _ => Some(align(value)),
    }
}

fn justify(value: Option<&Value>) -> JustifyContent {
    value.map_or(JustifyContent::NORMAL, |v| match v.as_str().unwrap() {
        "flex-start" => JustifyContent::FLEX_START,
        "normal" => JustifyContent::NORMAL,
        "stretch" => JustifyContent::STRETCH,
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

fn grid_nonnegative(value: &Value) -> f32 {
    let number = value.as_f64().unwrap_or_else(|| panic!("unsupported grid number {value}"));
    assert!(number.is_finite() && number >= 0.0 && number <= f32::MAX as f64, "unsupported grid number {value}");
    number as f32
}

fn grid_breadth(value: &Value) -> TrackSizingFunction {
    match value {
        Value::Number(_) => length(grid_nonnegative(value)),
        Value::String(s) if s == "auto" => auto(),
        Value::String(s) if s == "min-content" => min_content(),
        Value::String(s) if s == "max-content" => max_content(),
        Value::Object(o) if o.len() == 1 && o.contains_key("percent") => percent(grid_nonnegative(&o["percent"])),
        Value::Object(o) if o.len() == 1 && o.contains_key("fr") => fr(grid_nonnegative(&o["fr"])),
        _ => panic!("unsupported grid breadth {value}"),
    }
}

fn grid_track(value: &Value) -> TrackSizingFunction {
    if let Some(o) = value.as_object().filter(|o| o.contains_key("fitContent")) {
        assert!(o.len() == 1, "unsupported grid track {value}");
        let argument = match &o["fitContent"] {
            Value::Number(_) => LengthPercentage::length(grid_nonnegative(&o["fitContent"])),
            Value::Object(limit) if limit.len() == 1 && limit.contains_key("percent") =>
                LengthPercentage::percent(grid_nonnegative(&limit["percent"])),
            _ => panic!("unsupported fit-content argument {value}"),
        };
        fit_content(argument)
    } else if let Some(o) = value.as_object().filter(|o| o.contains_key("minmax")) {
        assert!(o.len() == 1, "unsupported grid track {value}");
        let pair = o["minmax"].as_array().filter(|p| p.len() == 2)
            .unwrap_or_else(|| panic!("minmax requires two breadths: {value}"));
        let minimum = grid_breadth(&pair[0]);
        assert!(!minimum.max.is_fr(), "grid minmax minimum cannot be flexible: {value}");
        let maximum = grid_breadth(&pair[1]);
        TrackSizingFunction { min: minimum.min, max: maximum.max }
    } else {
        grid_breadth(value)
    }
}

fn grid_template<S: taffy::style::CheapCloneStr>(value: Option<&Value>) -> Vec<GridTemplateComponent<S>> {
    match value {
        None => Vec::new(),
        Some(Value::String(s)) if s == "none" => Vec::new(),
        Some(Value::Array(tracks)) => {
            assert!(tracks.len() <= 10_000, "grid template exceeds 10000 tracks");
            tracks.iter().map(|v| GridTemplateComponent::Single(grid_track(v))).collect()
        }
        Some(other) => panic!("unsupported grid template {other}"),
    }
}

fn grid_auto_tracks(value: Option<&Value>) -> Vec<TrackSizingFunction> {
    match value {
        None => Vec::new(),
        Some(Value::Array(tracks)) => {
            assert!(!tracks.is_empty() && tracks.len() <= 10_000, "grid auto tracks require 1..10000 sizes");
            tracks.iter().map(grid_track).collect()
        }
        Some(value) => vec![grid_track(value)],
    }
}

fn grid_line_number(value: &Value) -> i16 {
    let number = value.as_f64().unwrap_or_else(|| panic!("unsupported grid line {value}"));
    assert!(number.is_finite() && number != 0.0 && number.fract() == 0.0, "grid line must be a nonzero integer: {value}");
    // Match the native protocol's placement-number clamp before narrowing for taffy.
    number.clamp(-10_000.0, 10_000.0) as i16
}

fn grid_placement(value: Option<&Value>) -> GridPlacement {
    match value {
        None => GridPlacement::Auto,
        Some(Value::String(s)) if s == "auto" => GridPlacement::Auto,
        Some(value @ Value::Number(_)) => line(grid_line_number(value)),
        Some(Value::Object(o)) if o.len() == 1 && o.contains_key("line") => line(grid_line_number(&o["line"])),
        Some(Value::Object(o)) if o.len() == 1 && o.contains_key("span") => {
            let count = grid_line_number(&o["span"]);
            assert!(count > 0, "grid span must be positive");
            span(count as u16)
        }
        Some(other) => panic!("unsupported grid placement {other}"),
    }
}

fn style(value: &Value) -> Style {
    let s = value.as_object().unwrap();
    for name in s.keys().filter(|name| name.starts_with("grid")) {
        assert!(matches!(name.as_str(),
            "gridTemplateRows" | "gridTemplateColumns" | "gridAutoRows" | "gridAutoColumns" |
            "gridAutoFlow" | "gridRowStart" | "gridRowEnd" | "gridColumnStart" | "gridColumnEnd"),
            "unsupported grid property {name}");
    }
    let num = |name: &str, default: f32| s.get(name).map(|v| v.as_f64().unwrap() as f32).unwrap_or(default);
    let border = edges(s, "border", length_percentage);
    Style {
        display: match s.get("display") {
            None => Display::Block,
            Some(Value::String(s)) if s == "block" => Display::Block,
            Some(Value::String(s)) if s == "flex" => Display::Flex,
            Some(Value::String(s)) if s == "grid" => Display::Grid,
            Some(Value::String(s)) if s == "none" => Display::None,
            Some(other) => panic!("unsupported display {other}"),
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
        align_self: self_align(s.get("alignSelf")),
        justify_items: align(s.get("justifyItems")),
        justify_self: self_align(s.get("justifySelf")),
        align_content: justify(s.get("alignContent")),
        justify_content: justify(s.get("justifyContent")),
        grid_template_columns: grid_template(s.get("gridTemplateColumns")),
        grid_template_rows: grid_template(s.get("gridTemplateRows")),
        grid_auto_columns: grid_auto_tracks(s.get("gridAutoColumns")),
        grid_auto_rows: grid_auto_tracks(s.get("gridAutoRows")),
        grid_auto_flow: match s.get("gridAutoFlow") {
            None => GridAutoFlow::Row,
            Some(Value::String(s)) if s == "row" => GridAutoFlow::Row,
            Some(Value::String(s)) if s == "column" => GridAutoFlow::Column,
            Some(Value::String(s)) if s == "row dense" => GridAutoFlow::RowDense,
            Some(Value::String(s)) if s == "column dense" => GridAutoFlow::ColumnDense,
            Some(other) => panic!("unsupported gridAutoFlow {other}"),
        },
        grid_column: Line { start: grid_placement(s.get("gridColumnStart")), end: grid_placement(s.get("gridColumnEnd")) },
        grid_row: Line { start: grid_placement(s.get("gridRowStart")), end: grid_placement(s.get("gridRowEnd")) },
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
            wrap: m.get("wrap").map(|v| v.as_bool().expect("wrap must be boolean")).unwrap_or(false),
            has_line_boxes: m.get("hasLineBoxes").map(|v| v.as_bool().expect("hasLineBoxes must be boolean")).unwrap_or(true),
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
    let height = known.height.unwrap_or_else(|| {
        let capacity = fixture.min_width.max(width);
        let lines = if fixture.wrap && fixture.has_line_boxes && capacity > 0.0 {
            (fixture.max_width / capacity).ceil().max(1.0)
        } else {
            1.0
        };
        fixture.height * lines
    });
    Size { width, height }
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

fn apply(workload: &mut Workload, step: &Value) {
    if let Some(viewport) = step.get("viewport") {
        let (width, height) = (viewport["width"].as_f64().unwrap() as f32, viewport["height"].as_f64().unwrap() as f32);
        let icb = Style { display: Display::Block, size: Size { width: length(width), height: length(height) }, ..Default::default() };
        workload.tree.set_style(workload.icb, icb).unwrap();
        workload.viewport = Size { width: AvailableSpace::Definite(width), height: AvailableSpace::Definite(height) };
    }
    for change in step.get("set").and_then(Value::as_array).into_iter().flatten() {
        let node = workload.nodes[change["node"].as_u64().unwrap() as usize];
        workload.tree.set_style(node, style(&change["style"])).unwrap();
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let document: Value = serde_json::from_slice(&std::fs::read(&args[1]).unwrap()).unwrap();
    let iterations: usize = args[2].parse().unwrap();
    let dump = args.iter().any(|a| a == "--dump");
    let updates = args.iter().position(|a| a == "--updates").map(|i| {
        let cycle: Value = serde_json::from_slice(&std::fs::read(&args[i + 1]).unwrap()).unwrap();
        cycle.as_array().unwrap().clone()
    });
    let mut samples = Vec::with_capacity(iterations);
    let mut last = build(&document);
    layout(&mut last);
    for iteration in 0..iterations {
        if let Some(cycle) = &updates {
            apply(&mut last, &cycle[iteration % cycle.len()]);
            let start = Instant::now();
            layout(&mut last);
            samples.push(start.elapsed().as_nanos() as u64);
            continue;
        }
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn grid_flow_preserves_axis_and_dense_hole_filling() {
        for (flow, span_axis, expected_position) in [
            ("row", "gridColumnEnd", (40.0, 20.0)),
            ("row dense", "gridColumnEnd", (40.0, 0.0)),
            ("column", "gridRowEnd", (20.0, 40.0)),
            ("column dense", "gridRowEnd", (0.0, 40.0)),
        ] {
            let root = json!({
                "display": "grid", "width": 60, "height": 60,
                "gridTemplateColumns": [20, 20, 20], "gridTemplateRows": [20, 20, 20],
                "gridAutoFlow": flow, "alignItems": "start", "justifyItems": "start"
            });
            let mut spanning = json!({"display": "block", "width": 10, "height": 10});
            spanning[span_axis] = json!({"span": 2});
            let document = json!({
                "viewport": {"width": 100, "height": 100},
                "nodes": [
                    {"parent": null, "style": root},
                    {"parent": 0, "style": spanning.clone()},
                    {"parent": 0, "style": spanning},
                    {"parent": 0, "style": {"display": "block", "width": 10, "height": 10}}
                ]
            });
            let mut workload = build(&document);
            layout(&mut workload);
            let position = workload.tree.layout(workload.nodes[3]).unwrap().location;
            assert_eq!((position.x, position.y), expected_position, "{flow}");
        }
    }

    #[test]
    fn unsupported_grid_syntax_is_not_approximated() {
        for unsupported in [
            json!({"gridAutoFlow": "dense"}),
            json!({"gridAutoFlow": "masonry"}),
            json!({"gridTemplateAreas": ["main"]}),
            json!({"gridTemplateColumns": [{"repeat": [2, 20]}]}),
            json!({"gridTemplateRows": "subgrid"}),
            json!({"gridRowStart": "main"}),
            json!({"gridColumnStart": {"line": 1, "name": "main"}}),
        ] {
            assert!(std::panic::catch_unwind(|| style(&unsupported)).is_err(), "{unsupported}");
        }
    }
}
