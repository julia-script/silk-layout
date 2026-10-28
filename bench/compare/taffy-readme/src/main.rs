//! Export taffy's README benchmark trees (same generators and seed) as silk-layout CLI documents.
use rand::Rng;
use serde_json::{json, Value};
use taffy::prelude::*;
use taffy::style::Style as TaffyStyle;
use taffy_benchmarks::{BuildTree, BuildTreeExt, FixedStyleGenerator, GenStyle, TaffyTreeBuilder};

fn random_dimension(rng: &mut impl Rng) -> Dimension {
    match rng.random_range(0.0..=1.0) {
        rand if rand < 0.2 => auto(),
        rand if rand < 0.8 => length(rng.random_range(0.0..500.0)),
        _ => percent(rng.random_range(0.0..1.0)),
    }
}
#[derive(Clone)]
struct RandomStyleGenerator;
impl GenStyle<TaffyStyle> for RandomStyleGenerator {
    fn create_leaf_style(&mut self, rng: &mut impl Rng) -> TaffyStyle {
        TaffyStyle { size: Size { width: random_dimension(rng), height: random_dimension(rng) }, ..Default::default() }
    }
    fn create_container_style(&mut self, rng: &mut impl Rng) -> TaffyStyle {
        TaffyStyle { size: Size { width: random_dimension(rng), height: random_dimension(rng) }, ..Default::default() }
    }
}
fn random_inset(rng: &mut impl Rng) -> LengthPercentageAuto {
    match rng.random_range(0.0..=1.0) {
        rand if rand < 0.4 => auto(),
        rand if rand < 0.8 => length(rng.random_range(0.0..500.0)),
        _ => percent(rng.random_range(0.0..1.0)),
    }
}
#[derive(Clone)]
struct RandomAbsoluteStyleGenerator;
impl GenStyle<TaffyStyle> for RandomAbsoluteStyleGenerator {
    fn create_leaf_style(&mut self, rng: &mut impl Rng) -> TaffyStyle {
        TaffyStyle {
            position: Position::Absolute,
            inset: Rect { left: random_inset(rng), right: random_inset(rng), top: random_inset(rng), bottom: random_inset(rng) },
            size: Size { width: random_dimension(rng), height: random_dimension(rng) },
            ..Default::default()
        }
    }
    fn create_container_style(&mut self, rng: &mut impl Rng) -> TaffyStyle {
        TaffyStyle { position: Position::Relative, size: Size { width: random_dimension(rng), height: random_dimension(rng) }, ..Default::default() }
    }
}
#[derive(Clone)]
struct SuperDeepStyleGen;
impl GenStyle<TaffyStyle> for SuperDeepStyleGen {
    fn create_leaf_style(&mut self, _rng: &mut impl Rng) -> TaffyStyle {
        Style { flex_direction: FlexDirection::Row, flex_grow: 1.0, margin: length(10.0), ..Default::default() }
    }
    fn create_container_style(&mut self, rng: &mut impl Rng) -> TaffyStyle { self.create_leaf_style(rng) }
}

fn dim(d: Dimension) -> Value {
    if d.is_auto() { return json!("auto") }
    let raw = d.into_raw();
    match raw.tag() { t if t == taffy::CompactLength::LENGTH_TAG => json!(raw.value()), t if t == taffy::CompactLength::PERCENT_TAG => json!({"percent": raw.value()}), _ => panic!() }
}
fn lpa(d: LengthPercentageAuto) -> Value {
    let raw = d.into_raw();
    if raw.is_auto() { return json!("auto") }
    match raw.tag() { t if t == taffy::CompactLength::LENGTH_TAG => json!(raw.value()), t if t == taffy::CompactLength::PERCENT_TAG => json!({"percent": raw.value()}), _ => panic!() }
}
fn style(s: &TaffyStyle, rel: bool) -> Value {
    assert!(s.display == Display::Flex && s.flex_direction == FlexDirection::Row);
    let mut o = json!({"display": "flex", "boxSizing": "border-box", "flexDirection": "row", "flexWrap": "nowrap",
        "flexShrink": s.flex_shrink, "flexGrow": s.flex_grow, "width": dim(s.size.width), "height": dim(s.size.height)});
    let m = o.as_object_mut().unwrap();
    let ml = lpa(s.margin.left);
    if ml != json!(0.0) { m.insert("margin".into(), ml); }
    // Every taffy node is position: relative, so in CSS each container holds its absolute children.
    if rel && s.position == Position::Relative { m.insert("position".into(), json!("relative")); }
    if s.position == Position::Absolute {
        m.insert("position".into(), json!("absolute"));
        m.insert("insetLeft".into(), lpa(s.inset.left)); m.insert("insetRight".into(), lpa(s.inset.right));
        m.insert("insetTop".into(), lpa(s.inset.top)); m.insert("insetBottom".into(), lpa(s.inset.bottom));
    }
    o
}
fn export<G: GenStyle<TaffyStyle>>(out: &str, name: &str, b: TaffyTreeBuilder<rand_chacha::ChaCha8Rng, G>) {
    let (tree, root) = b.into_tree_and_root();
    let mut order = vec![(root, Value::Null)];
    let mut nodes = vec![];
    let mut i = 0;
    while i < order.len() {
        let (n, parent) = order[i].clone();
        nodes.push(json!({"parent": parent, "style": style(tree.style(n).unwrap(), name.starts_with("absolute"))}));
        for c in tree.children(n).unwrap() { order.push((c, json!(i))); }
        i += 1;
    }
    let doc = json!({"viewport": {"width": 1000, "height": 800}, "nodes": nodes});
    std::fs::write(format!("{out}/{name}.json"), doc.to_string()).unwrap();
    eprintln!("{name}: {} nodes", nodes.len());
}

fn main() {
    let out = std::env::args().nth(1).expect("usage: taffy-readme-export OUT_DIR");
    std::fs::create_dir_all(&out).unwrap();
    let nested = Style { size: length(10.0), flex_grow: 1.0, ..Default::default() };
    let auto_style = Style { flex_grow: 1.0, margin: length(10.0), ..Default::default() };
    for n in [1_000u32, 10_000, 100_000] {
        let mut b = TaffyTreeBuilder::new(FixedStyleGenerator(nested.clone())); b.build_deep_hierarchy(n, 10); export(&out, &format!("huge-nested-{n}"), b);
        let mut b = TaffyTreeBuilder::new(RandomStyleGenerator); b.build_flat_hierarchy(n); export(&out, &format!("wide-{n}"), b);
        let mut b = TaffyTreeBuilder::new(RandomAbsoluteStyleGenerator); b.build_flat_hierarchy(n); export(&out, &format!("absolute-{n}"), b);
    }
    for n in [4_000u32, 10_000, 100_000] {
        let mut b = TaffyTreeBuilder::new(RandomStyleGenerator); b.build_deep_hierarchy(n, 2); export(&out, &format!("deep-random-{n}"), b);
        let mut b = TaffyTreeBuilder::new(FixedStyleGenerator(auto_style.clone())); b.build_deep_hierarchy(n, 2); export(&out, &format!("deep-auto-{n}"), b);
    }
    for d in [50u32, 100] {
        let mut b = TaffyTreeBuilder::new(SuperDeepStyleGen); b.build_super_deep_hierarchy(d, 3); export(&out, &format!("super-deep-{d}"), b);
    }
}
