// Lays out a silk-layout CLI workload document with yoga.
// Usage: yoga-harness WORKLOAD.json ITERATIONS [--dump] [--updates CYCLE.json]
// Prints {"layoutNs": [...]} and, with --dump, every node's border box in absolute coordinates.
// With --updates, one tree is kept: each iteration applies the next step of the cycle (a new
// viewport and/or node styles, as in the silk-layout CLI's "updates" field) and times one layout.
// A restyle sets every property its style names; the cycles only change values, never remove one.
// Yoga has no block layout: a block container becomes a column flex container whose children
// do not shrink, which matches block layout for the workloads here (no margin collapsing).
#include <yoga/Yoga.h>
#include <nlohmann/json.hpp>

#include <chrono>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

using json = nlohmann::json;

struct Fixture {
  float minWidth, maxWidth, height;
};

static std::vector<Fixture> fixtures;
// Whether each node's parent is a block container, whose children do not shrink.
static std::vector<bool> inBlock;

static YGSize measure(YGNodeConstRef node, float width, YGMeasureMode widthMode, float height, YGMeasureMode heightMode) {
  const Fixture& f = fixtures[reinterpret_cast<size_t>(YGNodeGetContext(node))];
  float w = f.maxWidth;
  if (widthMode == YGMeasureModeExactly) w = width;
  else if (widthMode == YGMeasureModeAtMost) w = std::max(f.minWidth, std::min(f.maxWidth, width));
  float h = heightMode == YGMeasureModeExactly ? height : f.height;
  return YGSize{w, h};
}

template <typename Length, typename Percent, typename Auto>
static void dimension(const json& style, const char* name, Length length, Percent percent, Auto autoFn) {
  if (!style.contains(name)) return;
  const json& v = style[name];
  if (v.is_number()) length(v.get<float>());
  else if (v.is_object()) percent(v["percent"].get<float>() * 100.0f);
  else if (v.is_string() && v.get<std::string>() == "auto") autoFn();
}

static void edges(YGNodeRef n, const json& s, const std::string& name,
                  void (*set)(YGNodeRef, YGEdge, float), void (*setPercent)(YGNodeRef, YGEdge, float)) {
  static const char* suffix[] = {"Left", "Top", "Right", "Bottom"};
  static const YGEdge edge[] = {YGEdgeLeft, YGEdgeTop, YGEdgeRight, YGEdgeBottom};
  for (int i = 0; i < 4; i++) {
    std::string side = name + suffix[i];
    const char* key = s.contains(side) ? side.c_str() : name.c_str();
    YGEdge e = edge[i];
    dimension(s, key, [&](float v) { set(n, e, v); }, [&](float v) { if (setPercent) setPercent(n, e, v); }, [] {});
  }
}

static YGAlign align(const std::string& v) {
  if (v == "flex-start" || v == "start") return YGAlignFlexStart;
  if (v == "flex-end" || v == "end") return YGAlignFlexEnd;
  if (v == "center") return YGAlignCenter;
  if (v == "baseline") return YGAlignBaseline;
  return YGAlignStretch;
}

static YGJustify justify(const std::string& v) {
  if (v == "flex-end" || v == "end") return YGJustifyFlexEnd;
  if (v == "center") return YGJustifyCenter;
  if (v == "space-between") return YGJustifySpaceBetween;
  if (v == "space-around") return YGJustifySpaceAround;
  if (v == "space-evenly") return YGJustifySpaceEvenly;
  return YGJustifyFlexStart;
}

static void applyStyle(YGNodeRef n, const json& s) {
  std::string display = s.value("display", "block");
  bool block = display != "flex";
  if (display == "none") YGNodeStyleSetDisplay(n, YGDisplayNone);
  YGNodeStyleSetFlexDirection(n, block || s.value("flexDirection", "row") == "column" ? YGFlexDirectionColumn : YGFlexDirectionRow);
  YGNodeStyleSetFlexWrap(n, s.value("flexWrap", "nowrap") == "wrap" ? YGWrapWrap : YGWrapNoWrap);
  YGNodeStyleSetFlexGrow(n, s.value("flexGrow", 0.0f));
  YGNodeStyleSetFlexShrink(n, s.value("flexShrink", 1.0f));
  dimension(s, "flexBasis", [&](float v) { YGNodeStyleSetFlexBasis(n, v); }, [&](float v) { YGNodeStyleSetFlexBasisPercent(n, v); }, [&] { YGNodeStyleSetFlexBasisAuto(n); });
  dimension(s, "width", [&](float v) { YGNodeStyleSetWidth(n, v); }, [&](float v) { YGNodeStyleSetWidthPercent(n, v); }, [&] { YGNodeStyleSetWidthAuto(n); });
  dimension(s, "height", [&](float v) { YGNodeStyleSetHeight(n, v); }, [&](float v) { YGNodeStyleSetHeightPercent(n, v); }, [&] { YGNodeStyleSetHeightAuto(n); });
  dimension(s, "minWidth", [&](float v) { YGNodeStyleSetMinWidth(n, v); }, [&](float v) { YGNodeStyleSetMinWidthPercent(n, v); }, [] {});
  dimension(s, "minHeight", [&](float v) { YGNodeStyleSetMinHeight(n, v); }, [&](float v) { YGNodeStyleSetMinHeightPercent(n, v); }, [] {});
  dimension(s, "maxWidth", [&](float v) { YGNodeStyleSetMaxWidth(n, v); }, [&](float v) { YGNodeStyleSetMaxWidthPercent(n, v); }, [] {});
  dimension(s, "maxHeight", [&](float v) { YGNodeStyleSetMaxHeight(n, v); }, [&](float v) { YGNodeStyleSetMaxHeightPercent(n, v); }, [] {});
  edges(n, s, "padding", YGNodeStyleSetPadding, YGNodeStyleSetPaddingPercent);
  edges(n, s, "margin", YGNodeStyleSetMargin, YGNodeStyleSetMarginPercent);
  edges(n, s, "border", YGNodeStyleSetBorder, nullptr);
  if (s.value("position", "") == "absolute") YGNodeStyleSetPositionType(n, YGPositionTypeAbsolute);
  edges(n, s, "inset", YGNodeStyleSetPosition, YGNodeStyleSetPositionPercent);
  dimension(s, "columnGap", [&](float v) { YGNodeStyleSetGap(n, YGGutterColumn, v); }, [&](float v) { YGNodeStyleSetGapPercent(n, YGGutterColumn, v); }, [] {});
  dimension(s, "rowGap", [&](float v) { YGNodeStyleSetGap(n, YGGutterRow, v); }, [&](float v) { YGNodeStyleSetGapPercent(n, YGGutterRow, v); }, [] {});
  if (s.contains("alignItems")) YGNodeStyleSetAlignItems(n, align(s["alignItems"]));
  if (s.contains("justifyContent")) YGNodeStyleSetJustifyContent(n, justify(s["justifyContent"]));
}
// nodes[0] is the initial containing block, a fixed-size block box holding the root;
// document node i is nodes[i + 1].
static std::vector<YGNodeRef> build(const json& doc, YGConfigRef config) {
  const json& items = doc["nodes"];
  std::vector<YGNodeRef> nodes;
  std::vector<bool> isBlock;
  YGNodeRef icb = YGNodeNewWithConfig(config);
  YGNodeStyleSetFlexDirection(icb, YGFlexDirectionColumn);
  YGNodeStyleSetWidth(icb, doc["viewport"]["width"].get<float>());
  YGNodeStyleSetHeight(icb, doc["viewport"]["height"].get<float>());
  nodes.push_back(icb);
  isBlock.push_back(true);
  nodes.reserve(items.size());
  fixtures.clear();
  inBlock.assign(1, false);
  for (const json& item : items) {
    YGNodeRef n = YGNodeNewWithConfig(config);
    const json& s = item["style"];
    std::string display = s.value("display", "block");
    bool block = display != "flex";
    applyStyle(n, s);
    if (item.contains("measure")) {
      const json& m = item["measure"];
      YGNodeSetContext(n, reinterpret_cast<void*>(fixtures.size()));
      fixtures.push_back({m["minWidth"].get<float>(), m["maxWidth"].get<float>(), m["height"].get<float>()});
      YGNodeSetMeasureFunc(n, measure);
    }
    {
      size_t parent = item["parent"].is_null() ? 0 : item["parent"].get<size_t>() + 1;
      if (isBlock[parent]) YGNodeStyleSetFlexShrink(n, 0.0f);
      inBlock.push_back(isBlock[parent]);
      YGNodeInsertChild(nodes[parent], n, YGNodeGetChildCount(nodes[parent]));
    }
    nodes.push_back(n);
    isBlock.push_back(block);
  }
  return nodes;
}

int main(int argc, char** argv) {
  std::ifstream in(argv[1]);
  json doc = json::parse(in);
  int iterations = std::atoi(argv[2]);
  bool dump = false;
  json cycle;
  for (int a = 3; a < argc; a++) {
    if (std::strcmp(argv[a], "--dump") == 0) dump = true;
    if (std::strcmp(argv[a], "--updates") == 0 && a + 1 < argc) {
      std::ifstream file(argv[a + 1]);
      cycle = json::parse(file);
    }
  }
  YGConfigRef config = YGConfigNew();
  YGConfigSetUseWebDefaults(config, true);
  YGConfigSetPointScaleFactor(config, 0.0f);
  float width = doc["viewport"]["width"].get<float>();
  float height = doc["viewport"]["height"].get<float>();
  std::vector<long long> samples;
  std::vector<YGNodeRef> last;
  if (cycle.is_array()) {
    last = build(doc, config);
    YGNodeCalculateLayout(last[0], width, height, YGDirectionLTR);
    for (int i = 0; i < iterations; i++) {
      const json& step = cycle[i % cycle.size()];
      if (step.contains("viewport")) {
        width = step["viewport"]["width"].get<float>();
        height = step["viewport"]["height"].get<float>();
        YGNodeStyleSetWidth(last[0], width);
        YGNodeStyleSetHeight(last[0], height);
      }
      if (step.contains("set")) {
        for (const json& change : step["set"]) {
          size_t index = change["node"].get<size_t>() + 1;
          applyStyle(last[index], change["style"]);
          if (inBlock[index]) YGNodeStyleSetFlexShrink(last[index], 0.0f);
        }
      }
      auto start = std::chrono::steady_clock::now();
      YGNodeCalculateLayout(last[0], width, height, YGDirectionLTR);
      auto end = std::chrono::steady_clock::now();
      samples.push_back(std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count());
    }
    iterations = -1;
  }
  for (int i = 0; i <= iterations; i++) {
    // A fresh tree per iteration: yoga caches layouts inside the tree, while silk-layout
    // recomputes everything on every call.
    std::vector<YGNodeRef> nodes = build(doc, config);
    auto start = std::chrono::steady_clock::now();
    YGNodeCalculateLayout(nodes[0], width, height, YGDirectionLTR);
    auto end = std::chrono::steady_clock::now();
    if (i > 0) samples.push_back(std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count());
    if (!last.empty()) YGNodeFreeRecursive(last[0]);
    last = nodes;
  }
  std::printf("{\"layoutNs\":[");
  for (size_t i = 0; i < samples.size(); i++) std::printf("%s%lld", i ? "," : "", samples[i]);
  std::printf("]");
  if (dump) {
    const json& items = doc["nodes"];
    std::vector<std::pair<float, float>> origin;
    std::printf(",\"boxes\":[");
    for (size_t i = 0; i < items.size(); i++) {
      YGNodeRef n = last[i + 1];
      float px = 0, py = 0;
      if (!items[i]["parent"].is_null()) {
        auto o = origin[items[i]["parent"].get<size_t>()];
        px = o.first;
        py = o.second;
      }
      float x = px + YGNodeLayoutGetLeft(n), y = py + YGNodeLayoutGetTop(n);
      origin.push_back({x, y});
      std::printf("%s[%g,%g,%g,%g]", i ? "," : "", x, y, YGNodeLayoutGetWidth(n), YGNodeLayoutGetHeight(n));
    }
    std::printf("]");
  }
  std::printf("}\n");
  return 0;
}
