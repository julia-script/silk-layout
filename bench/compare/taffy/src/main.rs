//! Lays out a silk-layout CLI workload document with taffy.
//! Usage: taffy-harness WORKLOAD.json ITERATIONS [--dump] [--updates CYCLE.json]
//! Prints {"layoutNs": [...]} and, with --dump, every node's border box in absolute coordinates.
//! With --updates, one tree is kept: each iteration applies the next step of the cycle (a new
//! viewport and/or node styles, as in the silk-layout CLI's "updates" field) and times one layout.
//! Grid maps parsed tracks, count/auto repeats, named lines/spans, area matrices,
//! all four auto-flow modes, gaps and item/self alignment to taffy's native APIs.
//! Subgrid, masonry, calc, shorthands and nonzero order are rejected rather than
//! approximated. Fixture wrapping models horizontal text without
//! floats; it does not expose line boxes or baseline data to taffy.
use serde_json::Value;
use std::time::Instant;
use taffy::prelude::*;
use taffy::style::{CheapCloneStr, GridTemplateArea, GridTemplateAreas, GridTemplateRepetition};

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

fn inset(value: Option<&Value>) -> LengthPercentageAuto {
    value.map_or(LengthPercentageAuto::auto(), |v| length_percentage_auto(Some(v)))
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

fn grid_name(value: &Value, area: bool) -> &str {
    let name = value.as_str().unwrap_or_else(|| panic!("grid name must be a string: {value}"));
    let start = |byte: u8| byte >= 128 || byte == b'_' || byte.is_ascii_alphabetic();
    let bytes = name.as_bytes();
    assert!(!bytes.is_empty() && bytes.iter().all(|&b| start(b) || b == b'-' || b.is_ascii_digit()),
        "invalid grid name {value}");
    if !area {
        assert!(start(bytes[0]) || bytes[0] == b'-' && bytes.len() > 1 && (start(bytes[1]) || bytes[1] == b'-'),
            "invalid grid identifier {value}");
        assert!(!["auto", "span", "default", "inherit", "initial", "unset", "revert", "revert-layer"]
            .iter().any(|keyword| name.eq_ignore_ascii_case(keyword)), "reserved grid identifier {value}");
    }
    name
}

fn grid_names<S: CheapCloneStr>(value: &Value, names: &mut Vec<S>) {
    let object = value.as_object().unwrap();
    assert!(object.len() == 1, "unsupported grid line names {value}");
    let list = object["names"].as_array().unwrap_or_else(|| panic!("grid names require an array: {value}"));
    names.extend(list.iter().map(|name| S::from(grid_name(name, false))));
}

fn fixed_repeat_track(track: &TrackSizingFunction) -> bool {
    // Of the supported breadths, only length/percentage minima are neither
    // intrinsic nor flexible. fit-content maxima are not definite values.
    (!track.min.is_intrinsic() && !track.min.is_fr()) || track.max.has_definite_value(Some(0.0))
}

fn grid_template<S: CheapCloneStr>(value: Option<&Value>) -> (Vec<GridTemplateComponent<S>>, Vec<Vec<S>>) {
    let entries = match value {
        None => return (Vec::new(), Vec::new()),
        Some(Value::String(s)) if s == "none" => return (Vec::new(), Vec::new()),
        Some(Value::Array(entries)) => entries,
        Some(other) => panic!("unsupported grid template {other}"),
    };
    let mut tracks = Vec::new();
    // Outer names are indexed by template components, not expanded tracks.
    let mut names = vec![Vec::new()];
    let mut expanded_count = 0usize;
    let mut automatic = false;
    let mut all_fixed = true;
    for entry in entries {
        if entry.get("names").is_some() {
            grid_names(entry, names.last_mut().unwrap());
            continue;
        }
        let component = if let Some(repeat) = entry.get("repeat") {
            assert!(entry.as_object().unwrap().len() == 1, "unsupported grid repeat {entry}");
            let object = repeat.as_object().unwrap_or_else(|| panic!("grid repeat requires an object: {entry}"));
            assert!(object.len() == 2 && object.contains_key("count") && object.contains_key("tracks"),
                "grid repeat requires count and tracks: {entry}");
            let count = match &object["count"] {
                Value::String(s) if s == "auto-fill" || s == "auto-fit" => {
                    assert!(!automatic, "only one automatic repeat is allowed");
                    automatic = true;
                    if s == "auto-fill" { RepetitionCount::AutoFill } else { RepetitionCount::AutoFit }
                }
                number => {
                    let count = number.as_f64().unwrap_or_else(|| panic!("unsupported grid repeat count {number}"));
                    assert!(count.fract() == 0.0 && (1.0..=10_000.0).contains(&count), "grid repeat count must be 1..10000");
                    RepetitionCount::Count(count as u16)
                }
            };
            let pattern = object["tracks"].as_array().unwrap_or_else(|| panic!("grid repeat tracks require an array: {entry}"));
            let mut repeated = Vec::new();
            let mut line_names = vec![Vec::new()];
            for part in pattern {
                if part.get("names").is_some() {
                    grid_names(part, line_names.last_mut().unwrap());
                } else {
                    // grid_track rejects nested repeats.
                    let track = grid_track(part);
                    all_fixed &= fixed_repeat_track(&track);
                    repeated.push(track);
                    line_names.push(Vec::new());
                }
            }
            assert!(!repeated.is_empty(), "grid repeat requires at least one track");
            expanded_count += repeated.len() * match count { RepetitionCount::Count(n) => n as usize, _ => 1 };
            GridTemplateComponent::Repeat(GridTemplateRepetition { count, tracks: repeated, line_names })
        } else {
            let track = grid_track(entry);
            all_fixed &= fixed_repeat_track(&track);
            expanded_count += 1;
            GridTemplateComponent::Single(track)
        };
        assert!(expanded_count <= 10_000, "grid template exceeds 10000 tracks");
        tracks.push(component);
        names.push(Vec::new());
    }
    assert!(!tracks.is_empty(), "grid template requires at least one track or none");
    assert!(!automatic || all_fixed, "automatic repeat requires fixed-size tracks throughout the template");
    (tracks, names)
}

fn grid_areas<S: CheapCloneStr>(value: Option<&Value>) -> Option<GridTemplateAreas<S>> {
    let value = value?;
    let rows = value.as_array().unwrap_or_else(|| panic!("grid areas require a matrix: {value}"));
    assert!(rows.len() <= 10_000, "grid areas exceed 10000 rows");
    let columns = rows.first().map(|row| row.as_array().expect("grid area row requires an array").len()).unwrap_or(0);
    assert!(rows.is_empty() || (1..=10_000).contains(&columns), "grid areas require 1..10000 columns");
    // Convert the protocol's cell matrix to native area rectangles. Placement
    // and the generated area line names remain entirely taffy's responsibility.
    let mut bounds = std::collections::BTreeMap::<&str, (usize, usize, usize, usize, usize)>::new();
    for (y, row) in rows.iter().enumerate() {
        let cells = row.as_array().expect("grid area row requires an array");
        assert!(cells.len() == columns, "grid areas must be a rectangular matrix");
        for (x, cell) in cells.iter().enumerate() {
            if cell.as_str() == Some(".") { continue; }
            let name = grid_name(cell, true);
            let area = bounds.entry(name).or_insert((y, y + 1, x, x + 1, 0));
            area.1 = y + 1;
            area.2 = area.2.min(x);
            area.3 = area.3.max(x + 1);
            area.4 += 1;
        }
    }
    let areas = bounds.into_iter().map(|(name, (top, bottom, left, right, count))| {
        assert!(count == (bottom - top) * (right - left), "each named grid area must fill a rectangle");
        GridTemplateArea {
            name: S::from(name), row_start: (top + 1) as u16, row_end: (bottom + 1) as u16,
            column_start: (left + 1) as u16, column_end: (right + 1) as u16,
        }
    }).collect();
    Some(GridTemplateAreas { areas, row_count: rows.len() as u16, column_count: columns as u16 })
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
        Some(value @ Value::String(_)) => GridPlacement::NamedLine(grid_name(value, false).into(), 0),
        Some(Value::Object(o)) if o.contains_key("line") || o.contains_key("span") => {
            let is_span = o.contains_key("span");
            let key = if is_span { "span" } else { "line" };
            assert!(o.keys().all(|name| name == key || name == "name"), "unsupported grid placement {}", value.unwrap());
            let count = grid_line_number(&o[key]);
            assert!(!is_span || count > 0, "grid span must be positive");
            match (is_span, o.get("name")) {
                (false, Some(name)) => GridPlacement::NamedLine(grid_name(name, false).into(), count),
                (true, Some(name)) => GridPlacement::NamedSpan(grid_name(name, false).into(), count as u16),
                (false, None) => line(count),
                (true, None) => span(count as u16),
            }
        }
        Some(other) => panic!("unsupported grid placement {other}"),
    }
}

fn style(value: &Value) -> Style {
    let s = value.as_object().unwrap();
    for name in s.keys().filter(|name| name.starts_with("grid")) {
        assert!(matches!(name.as_str(),
            "gridTemplateRows" | "gridTemplateColumns" | "gridAutoRows" | "gridAutoColumns" |
            "gridTemplateAreas" | "gridAutoFlow" | "gridRowStart" | "gridRowEnd" | "gridColumnStart" | "gridColumnEnd"),
            "unsupported grid property {name}");
    }
    assert!(s.get("order").is_none_or(|value| value.as_f64() == Some(0.0)), "nonzero order is unsupported by the taffy harness");
    let (columns, column_names) = grid_template(s.get("gridTemplateColumns"));
    let (rows, row_names) = grid_template(s.get("gridTemplateRows"));
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
        position: match s.get("position").and_then(Value::as_str) {
            Some("absolute") => Position::Absolute,
            _ => Position::Relative,
        },
        inset: edges(s, "inset", inset),
        border,
        gap: Size { width: length_percentage(s.get("columnGap")), height: length_percentage(s.get("rowGap")) },
        align_items: align(s.get("alignItems")),
        align_self: self_align(s.get("alignSelf")),
        justify_items: align(s.get("justifyItems")),
        justify_self: self_align(s.get("justifySelf")),
        align_content: justify(s.get("alignContent")),
        justify_content: justify(s.get("justifyContent")),
        grid_template_columns: columns,
        grid_template_rows: rows,
        grid_template_column_names: column_names,
        grid_template_row_names: row_names,
        grid_template_areas: grid_areas(s.get("gridTemplateAreas")),
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
    fn named_repeat_boundaries_and_spans_reach_layout() {
        let document = json!({
            "viewport": {"width": 200, "height": 100},
            "nodes": [
                {"parent": null, "style": {
                    "display": "grid", "width": 110, "height": 20,
                    "gridTemplateColumns": [
                        {"names": ["outer"]},
                        {"repeat": {"count": 2, "tracks": [
                            {"names": ["edge"]}, 20, {"names": ["middle"]}, 30, {"names": ["edge"]}
                        ]}},
                        {"names": ["tail"]}, 10
                    ],
                    "gridTemplateRows": [20]
                }},
                {"parent": 0, "style": {"gridColumnStart": {"line": 2, "name": "edge"}, "gridRowStart": 1}},
                {"parent": 0, "style": {
                    "gridColumnStart": "outer", "gridColumnEnd": {"span": 2, "name": "middle"}, "gridRowStart": 1
                }},
                {"parent": 0, "style": {"gridColumnStart": {"line": -1, "name": "edge"}, "gridRowStart": 1}},
                {"parent": 0, "style": {"gridColumnStart": "tail", "gridRowStart": 1}}
            ]
        });
        let mut workload = build(&document);
        layout(&mut workload);
        for (index, x, width) in [(1, 50.0, 20.0), (2, 0.0, 70.0), (3, 100.0, 10.0), (4, 100.0, 10.0)] {
            let rect = workload.tree.layout(workload.nodes[index]).unwrap();
            assert_eq!((rect.location.x, rect.size.width), (x, width));
        }
    }

    #[test]
    fn area_aliases_and_unnamed_template_extent_reach_layout() {
        for (areas, placement, expected) in [
            (json!([["side", "main", "main"], ["side", "main", "main"]]),
             json!({"gridColumnStart": "main", "gridColumnEnd": "main", "gridRowStart": "main", "gridRowEnd": "main"}),
             (20.0, 0.0, 50.0, 40.0)),
            (json!([[".", ".", "."], [".", ".", "."]]),
             json!({"gridColumnStart": -2, "gridColumnEnd": -1, "gridRowStart": -2, "gridRowEnd": -1}),
             (50.0, 20.0, 20.0, 20.0)),
        ] {
            let document = json!({
                "viewport": {"width": 100, "height": 100},
                "nodes": [
                    {"parent": null, "style": {
                        "display": "grid", "width": 100, "height": 100,
                        "gridTemplateAreas": areas,
                        "gridAutoColumns": [20, 30], "gridAutoRows": 20,
                        "justifyContent": "start", "alignContent": "start"
                    }},
                    {"parent": 0, "style": placement}
                ]
            });
            let mut workload = build(&document);
            layout(&mut workload);
            let rect = workload.tree.layout(workload.nodes[1]).unwrap();
            assert_eq!((rect.location.x, rect.location.y, rect.size.width, rect.size.height), expected);
        }
    }

    #[test]
    fn auto_repeat_preserves_empty_tracks_only_for_auto_fill() {
        for (count, expected_width) in [("auto-fill", 80.0 / 3.0), ("auto-fit", 100.0)] {
            let document = json!({
                "viewport": {"width": 100, "height": 100},
                "nodes": [
                    {"parent": null, "style": {
                        "display": "grid", "width": 100, "height": 20,
                        "gridTemplateColumns": [{"repeat": {"count": count, "tracks": [{"minmax": [20, {"fr": 1}]}]}}],
                        "gridTemplateRows": [20], "columnGap": 10
                    }},
                    {"parent": 0, "style": {}}
                ]
            });
            let mut workload = build(&document);
            layout(&mut workload);
            let rect = workload.tree.layout(workload.nodes[1]).unwrap();
            assert!((rect.size.width - expected_width).abs() < 0.001, "{count}: {rect:?}");
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
            json!({"gridRowStart": "span"}),
            json!({"gridColumnStart": {"line": 0, "name": "main"}}),
            json!({"gridColumnEnd": {"span": -1, "name": "main"}}),
            json!({"gridColumnStart": {"line": 1, "name": "main", "extra": true}}),
            json!({"gridTemplateColumns": [{"names": ["bad name"]}, 20]}),
            json!({"gridTemplateColumns": [{"repeat": {"count": 0, "tracks": [20]}}]}),
            json!({"gridTemplateColumns": [{"repeat": {"count": 1.5, "tracks": [20]}}]}),
            json!({"gridTemplateColumns": [{"repeat": {"count": 2, "tracks": []}}]}),
            json!({"gridTemplateColumns": [{"repeat": {"count": 2, "tracks": [{"repeat": {"count": 2, "tracks": [20]}}]}}]}),
            json!({"gridTemplateColumns": [{"repeat": {"count": 10000, "tracks": [20, 30]}}]}),
            json!({"gridTemplateColumns": [{"repeat": {"count": "auto-fill", "tracks": [{"fr": 1}]}}]}),
            json!({"gridTemplateColumns": ["auto", {"repeat": {"count": "auto-fit", "tracks": [20]}}]}),
            json!({"gridTemplateColumns": [
                {"repeat": {"count": "auto-fill", "tracks": [20]}},
                {"repeat": {"count": "auto-fit", "tracks": [20]}}
            ]}),
            json!({"gridTemplateColumns": [{"percent": 0.5, "length": 20}]}),
            json!({"gridTemplateAreas": [["a", "a"], ["a", "."]]}),
            json!({"gridTemplateAreas": [["a"], ["b", "b"]]}),
            json!({"gridTemplateAreas": [["bad name"]]}),
            json!({"order": 1}),
        ] {
            assert!(std::panic::catch_unwind(|| style(&unsupported)).is_err(), "{unsupported}");
        }
    }
}
