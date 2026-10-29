#!/usr/bin/env node
// Independent Chromium observations are regression evidence, not the specification.
// Usage: node scripts/chrome-diff.mjs --cli PATH [fixture.json | fixture-directory]
//        --mode all|spec|chrome --chrome PATH --chrome-version Chrome/VERSION
//        --tolerance 0.05 --fuzz 100 --grid-fuzz 100 --seed 1
import { spawn, spawnSync } from 'node:child_process';
import { access, mkdtemp, readdir, readFile, rm, mkdir, writeFile } from 'node:fs/promises';
import { constants } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const geometryFields = ['x', 'y', 'width', 'height', 'contentWidth', 'contentHeight'];
const rectangleFields = ['x', 'y', 'width', 'height'];
const overflowFields = ['scrollableOverflowX', 'scrollableOverflowY', 'scrollableOverflowWidth', 'scrollableOverflowHeight'];
const gutterFields = ['scrollbarGutterTop', 'scrollbarGutterRight', 'scrollbarGutterBottom', 'scrollbarGutterLeft'];
const booleanFields = ['hasFirstBaseline', 'hasLastBaseline', 'horizontalScrollbar', 'verticalScrollbar'];
const usedFields = [...geometryFields, ...booleanFields, 'measurementOffsetX', 'measurementOffsetY', ...overflowFields, ...gutterFields];
const sourceFields = [...usedFields, 'firstBaseline', 'lastBaseline'];
const fontBaselineFields = ['alphabetic', 'ideographic', 'central', 'mathematical'];
const baselineFields = [...fontBaselineFields, 'xMiddle', 'textOver', 'textUnder'];
const options = { cli: process.env.SILK_LAYOUT_CLI, chrome: process.env.CHROME_PATH,
  chromeVersion: process.env.CHROME_VERSION, mode: 'all', tolerance: 0.05,
  fuzz: 0, gridFuzz: 0, seed: 1, path: join(root, 'tests/fixtures') };
const summary = { schemaVersion: 3, tolerance: options.tolerance, seed: 1, browser: null,
  totals: { discovered: 0, passed: 0, failed: 0, excludedUnsupported: 0,
    spec: 0, chrome: 0, fuzz: 0, mismatches: 0 },
  comparison: { chromeGeometry: geometryFields, chromeBaselines: 'Measured leaf baselines only; other baseline assertions belong to spec fixtures.',
    chromeResources: 'Loaded and decoded data-SVG images. Resource records retain exact supplied metadata and generated markup, not HTMLImageElement.naturalWidth/naturalHeight, which can synthesize missing SVG dimensions. Two absolute SVG dimensions determine the ratio; incompatible independent triples are unsupported and exit nonzero.' },
  scrollbarObservation: 'Classic content boxes and gutters use CDP content-box quads, not rounded CSSOM client dimensions. Host-policy mismatches and inconsistent tiny mirrored geometry are unsupported. Scrollbar-width:thin cannot represent custom classic metrics.',
  results: [], errors: [] };
let browser;
const fixtureFontCache = new Map();

async function fixtureFonts(input) {
  if (!input.nodes.some(node => Object.hasOwn(node, 'text'))) return null;
  const request = JSON.stringify({ fonts: input.fonts ?? [] });
  if (fixtureFontCache.has(request)) return fixtureFontCache.get(request);
  let python = process.env.SILK_FONT_PYTHON;
  if (!python) {
    python = join(root, '.cache/font-tools/bin/python');
    try { await access(python, constants.X_OK); } catch { python = 'python3'; }
  }
  const result = spawnSync(python, [join(root, 'scripts/fixture-font.py')], {
    input: request, encoding: 'utf8', maxBuffer: 32 * 1024 * 1024
  });
  if (result.error || result.status !== 0) {
    throw new Error(`Authored fixture-font backend failed: ${result.error?.message ?? result.stderr?.trim() ?? `exit ${result.status}`}`);
  }
  let assets;
  try { assets = JSON.parse(result.stdout); } catch { throw new Error('Authored fixture-font backend returned invalid JSON'); }
  if (!Array.isArray(assets.fonts) || !Array.isArray(assets.unsupported)) throw new Error('Authored fixture-font backend returned an invalid asset manifest');
  const keys = new Set();
  for (const record of [...assets.fonts, ...assets.unsupported]) {
    if (!Number.isInteger(record.key) || record.key < 1 || record.key > (input.fonts?.length ?? 0) || keys.has(record.key)) {
      throw new Error('Authored fixture-font backend returned an invalid or duplicate font key');
    }
    keys.add(record.key);
  }
  if (keys.size !== (input.fonts?.length ?? 0)) throw new Error('Authored fixture-font backend omitted font profiles');
  fixtureFontCache.set(request, assets);
  return assets;
}

function parseArgs() {
  let positional = false;
  const values = { '--cli': 'cli', '--chrome': 'chrome', '--chrome-version': 'chromeVersion',
    '--mode': 'mode', '--tolerance': 'tolerance', '--fuzz': 'fuzz', '--grid-fuzz': 'gridFuzz', '--seed': 'seed' };
  for (let i = 2; i < process.argv.length; i++) {
    const arg = process.argv[i];
    if (arg === '--help' || arg === '-h') {
      process.stdout.write('Usage: node scripts/chrome-diff.mjs --cli PATH [fixture.json|directory]\n  --mode all|spec|chrome --chrome PATH --chrome-version Chrome/VERSION\n  --tolerance 0.05 --fuzz N --grid-fuzz N --seed INTEGER\nSpec fixtures assert hand-derived expectations without Chrome. Differential failures\nare never excluded. Unsupported browser adapters are reported and exit nonzero.\nGrid fuzz uses an independent seeded stream; --fuzz inputs are unchanged.\nBoth fuzz modes retain failures and leaf-reduced reproductions in .cache/chrome-diff.\n');
      return false;
    }
    if (values[arg]) {
      if (++i === process.argv.length) throw new Error(`Missing value for ${arg}`);
      options[values[arg]] = process.argv[i];
    } else if (arg.startsWith('-') || positional) throw new Error(`Unexpected argument ${arg}`);
    else { options.path = resolve(arg); positional = true; }
  }
  options.tolerance = Number(options.tolerance);
  options.fuzz = Number(options.fuzz);
  options.gridFuzz = Number(options.gridFuzz);
  options.seed = Number(options.seed);
  if (!Number.isFinite(options.tolerance) || options.tolerance < 0) throw new Error('Tolerance must be finite and nonnegative');
  if (!Number.isSafeInteger(options.fuzz) || options.fuzz < 0) throw new Error('--fuzz must be a nonnegative safe integer');
  if (!Number.isSafeInteger(options.gridFuzz) || options.gridFuzz < 0) throw new Error('--grid-fuzz must be a nonnegative safe integer');
  if (!Number.isInteger(options.seed) || options.seed < 0 || options.seed > 0xffffffff) throw new Error('--seed must be an unsigned 32-bit integer');
  if (!['all', 'spec', 'chrome'].includes(options.mode)) throw new Error('Invalid --mode');
  if (options.mode === 'spec' && options.fuzz) throw new Error('--fuzz requires Chrome mode');
  if (options.mode === 'spec' && options.gridFuzz) throw new Error('--grid-fuzz requires Chrome mode');
  if (!options.cli) throw new Error('Supply the prebuilt native CLI using --cli PATH or SILK_LAYOUT_CLI; this harness never builds it');
  options.cli = resolve(options.cli);
  summary.tolerance = options.tolerance;
  summary.seed = options.seed;
  return true;
}

async function fixtureFiles(path) {
  const { stat } = await import('node:fs/promises');
  if ((await stat(path)).isFile()) return [path];
  const result = [];
  for (const entry of (await readdir(path, { withFileTypes: true })).sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0)) {
    if (entry.isDirectory()) result.push(...await fixtureFiles(join(path, entry.name)));
    else if (entry.isFile() && entry.name.endsWith('.json')) result.push(join(path, entry.name));
  }
  return result;
}

function validateScrollbars(value) {
  const object = (record, keys, path) => {
    if (!record || typeof record !== 'object' || Array.isArray(record)
      || Object.keys(record).length !== keys.length || keys.some(key => !Object.hasOwn(record, key))) {
      throw new Error(`${path} requires exactly ${keys.join(', ')}`);
    }
  };
  object(value, ['auto', 'thin'], 'scrollbars');
  for (const width of ['auto', 'thin']) {
    const record = value[width], path = `scrollbars.${width}`;
    object(record, ['horizontal', 'vertical', 'overlay', 'horizontalOnTop', 'verticalOnLeft'], path);
    for (const key of ['horizontal', 'vertical']) {
      if (!Number.isFinite(record[key]) || record[key] < 0) throw new Error(`${path}.${key} must be finite and nonnegative`);
    }
    for (const key of ['overlay', 'horizontalOnTop', 'verticalOnLeft']) {
      if (typeof record[key] !== 'boolean') throw new Error(`${path}.${key} must be boolean`);
    }
  }
}

function validateFragmentation(value) {
  const object = (entry, keys, required = keys) => entry && typeof entry === 'object' && !Array.isArray(entry)
    && Object.keys(entry).every(key => keys.includes(key)) && required.every(key => Object.hasOwn(entry, key));
  if (!object(value, ['kind', 'frames', 'repeatStep', 'writingMode', 'direction', 'firstPage', 'recto', 'contexts', 'ancestors'], ['kind', 'frames'])
    || !['column', 'page', 'region'].includes(value.kind)
    || !Array.isArray(value.frames) || !value.frames.length) throw new Error('fragmentation requires a kind and nonempty frames array');
  value.frames.forEach((frame, index) => {
    if (!object(frame, rectangleFields) || rectangleFields.some(key => !Number.isFinite(frame[key]))
      || frame.width < 0 || frame.height < 0) throw new Error(`Invalid fragmentation.frames[${index}]`);
  });
  if (Object.hasOwn(value, 'contexts') !== Object.hasOwn(value, 'ancestors')) throw new Error('fragmentation contexts and ancestors must be supplied together');
  if (Object.hasOwn(value, 'contexts')) {
    if (!Array.isArray(value.contexts) || value.contexts.some(kind => !['page', 'column', 'region'].includes(kind))
      || !Array.isArray(value.ancestors) || value.ancestors.length !== value.frames.length) throw new Error('Invalid fragmentation context ancestry');
    value.ancestors.forEach((row, frame) => {
      if (!Array.isArray(row) || row.length !== value.contexts.length) throw new Error(`Invalid fragmentation.ancestors[${frame}]`);
      let parentChanged = false;
      row.forEach((entry, depth) => {
        if (!object(entry, ['index', ...rectangleFields]) || !Number.isInteger(entry.index) || entry.index < 0 || entry.index > 2147483647
          || rectangleFields.some(key => !Number.isFinite(entry[key])) || entry.width < 0 || entry.height < 0) throw new Error(`Invalid fragmentation ancestor ${frame}:${depth}`);
        if (frame === 0) {
          if (entry.index !== 0) throw new Error('Fragment ancestor ordinals must begin at zero');
        } else {
          const previous = value.ancestors[frame - 1][depth], changed = entry.index !== previous.index;
          if (changed && entry.index !== previous.index + 1 || parentChanged && !changed) throw new Error('Fragment ancestor ordinals must advance consecutively with their parents');
          if (!changed && rectangleFields.some(key => entry[key] !== previous[key])) throw new Error('An unchanged ancestor must retain its physical geometry');
          parentChanged = changed;
        }
      });
    });
  }
  if (Object.hasOwn(value, 'repeatStep') && (!object(value.repeatStep, ['x', 'y'])
    || !Number.isFinite(value.repeatStep.x) || !Number.isFinite(value.repeatStep.y))) throw new Error('fragmentation.repeatStep requires finite x and y');
  for (const [key, values] of [
    ['writingMode', ['horizontal-tb', 'vertical-rl', 'vertical-lr', 'sideways-rl', 'sideways-lr']],
    ['direction', ['ltr', 'rtl']], ['firstPage', ['left', 'right']], ['recto', ['left', 'right']]
  ]) {
    if (Object.hasOwn(value, key) && !values.includes(value[key])) throw new Error(`Invalid fragmentation.${key}`);
  }
}

// CSS multicol supplies uniform columns progressing along the inline axis.
// It cannot model page/region semantics, varying rectangles, or arbitrary steps.
function columnGeometry(input) {
  const value = input.fragmentation;
  if (!value) return null;
  const unsupported = reason => ({ unsupported: reason });
  if (value.contexts?.length) return unsupported('Host-supplied nested contexts require spec-only fixtures; Chrome multicol cannot substitute their ancestry.');
  if (value.kind !== 'column') return unsupported('Page and region fragmentation require spec-only fixtures; Chrome multicol is not their oracle.');
  const first = value.frames[0], mode = value.writingMode ?? 'horizontal-tb', direction = value.direction ?? 'ltr';
  if (first.width <= 0 || first.height <= 0) return unsupported('Zero-extent fragmentainers require spec-only fixtures.');
  const horizontal = mode === 'horizontal-tb', axis = horizontal ? 'x' : 'y', cross = horizontal ? 'y' : 'x';
  const extent = horizontal ? first.width : first.height;
  const sign = (direction === 'rtl' ? -1 : 1) * (mode === 'sideways-lr' ? -1 : 1);
  const step = value.frames.length > 1
    ? { x: value.frames[1].x - first.x, y: value.frames[1].y - first.y }
    : value.repeatStep ?? { [axis]: sign * extent, [cross]: 0 };
  if (step[cross] !== 0 || step[axis] * sign < extent
    || value.frames.some((frame, index) => frame.width !== first.width || frame.height !== first.height
      || frame.x !== first.x + step.x * index || frame.y !== first.y + step.y * index)
    || value.repeatStep && (value.repeatStep.x !== step.x || value.repeatStep.y !== step.y)) {
    return unsupported('Nonuniform or non-inline-progressing frames require spec-only fixtures, not a substituted Chrome geometry.');
  }
  return { ...first, mode, direction, gap: step[axis] * sign - extent, step };
}

function validateInput(input) {
  if (!input || !input.viewport || !Array.isArray(input.nodes) || !input.nodes.length) throw new Error('Protocol requires viewport and a nonempty nodes array');
  for (const key of ['width', 'height']) {
    const value = input.viewport[key];
    if (!Number.isFinite(value) || value <= 0 || !Number.isInteger(value)) throw new Error(`Chrome viewport.${key} must be a positive integer`);
  }
  const scrollOffset = (value, path) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).some(key => !['x', 'y'].includes(key))
      || !Number.isFinite(value.x) || !Number.isFinite(value.y)) {
      throw new Error(`${path} requires exactly finite numeric x and y fields`);
    }
  };
  if (Object.hasOwn(input, 'viewportScrollOffset')) scrollOffset(input.viewportScrollOffset, 'viewportScrollOffset');
  if (Object.hasOwn(input, 'fragmentation')) validateFragmentation(input.fragmentation);
  if (Object.hasOwn(input, 'scrollbars')) validateScrollbars(input.scrollbars);
  if (Object.hasOwn(input, 'fonts') && !Array.isArray(input.fonts)) throw new Error('fonts must be an array');
  const fontFields = ['fontSize', 'ascent', 'descent', 'lineGap', 'xHeight', 'capHeight', 'subscriptOffset', 'superscriptOffset'];
  const exactFields = (value, fields) => value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === fields.length && fields.every(field => Object.hasOwn(value, field));
  for (const [index, font] of (input.fonts ?? []).entries()) {
    if (!exactFields(font, [...fontFields, 'baselines']) || !exactFields(font.baselines, fontBaselineFields)
      || fontFields.some(field => !Number.isFinite(font[field]))
      || ['fontSize', 'ascent', 'descent', 'xHeight', 'capHeight'].some(field => font[field] < 0)
      || fontBaselineFields.some(field => !Number.isFinite(font.baselines[field]))) throw new Error(`Invalid fonts[${index}] metrics`);
  }
  input.nodes.forEach((node, index) => {
    if (!node || typeof node !== 'object' || Array.isArray(node)) throw new Error(`Invalid node ${index}`);
    if (index === 0 ? node.parent !== null : !Number.isInteger(node.parent) || node.parent < 0 || node.parent >= index) throw new Error(`Invalid parent at node ${index}`);
    if (index > 0 && Object.hasOwn(input.nodes[node.parent], 'text')) throw new Error(`Text node cannot parent node ${index}`);
    if (Object.hasOwn(node, 'text')) {
      if (index === 0 || typeof node.text !== 'string' || Object.keys(node).some(key => !['parent', 'text'].includes(key))) throw new Error(`Node ${index} requires exactly parent and text; root must be an element`);
      return;
    }
    if (!node.style || typeof node.style !== 'object' || Array.isArray(node.style)
      || Object.keys(node).some(key => !['parent', 'style', 'measure', 'natural', 'scrollOffset'].includes(key))) throw new Error(`Node ${index} requires an element style object`);
    if (Object.hasOwn(node.style, 'whiteSpace')) throw new Error(`Node ${index}.style.whiteSpace is not supported; use whiteSpaceCollapse and textWrap`);
    if (Object.hasOwn(node.style, 'tabSize')) {
      const size = node.style.tabSize;
      if (!(Number.isFinite(size) && size >= 0)
        && !(exactFields(size, ['length']) && Number.isFinite(size.length) && size.length >= 0)) throw new Error(`Invalid node ${index}.style.tabSize`);
    }
    if (Object.hasOwn(node.style, 'textIndent')) {
      const indent = node.style.textIndent;
      const wrapped = indent && typeof indent === 'object' && !Array.isArray(indent) && Object.hasOwn(indent, 'value');
      if (wrapped && (Object.keys(indent).some(key => !['value', 'hanging', 'eachLine'].includes(key))
        || ['hanging', 'eachLine'].some(key => Object.hasOwn(indent, key) && typeof indent[key] !== 'boolean'))) throw new Error(`Invalid node ${index}.style.textIndent options`);
      const amount = wrapped ? indent.value : indent;
      if (!Number.isFinite(amount)
        && !(exactFields(amount, ['percent']) && Number.isFinite(amount.percent))
        && !(exactFields(amount, ['length', 'percent']) && Number.isFinite(amount.length) && Number.isFinite(amount.percent))) throw new Error(`Invalid node ${index}.style.textIndent amount`);
    }
    const fontKey = node.style.fontKey ?? 0;
    if (!Number.isInteger(fontKey) || fontKey < 0 || fontKey > (input.fonts?.length ?? 0)) throw new Error(`Invalid node ${index}.style.fontKey`);
    if (Object.hasOwn(node.style, 'lineHeight')) {
      const height = node.style.lineHeight;
      if (height !== 'normal' && !(Number.isFinite(height) && height >= 0)
        && !(exactFields(height, ['length']) && Number.isFinite(height.length) && height.length >= 0)) throw new Error(`Invalid node ${index}.style.lineHeight`);
    }
    for (const key of ['orphans', 'widows']) {
      if (Object.hasOwn(node.style, key)
        && (!Number.isInteger(node.style[key]) || node.style[key] <= 0 || node.style[key] >= 2 ** 64)) {
        throw new Error(`Invalid node ${index}.style.${key}: expected a positive usize integer`);
      }
    }
    if (Object.hasOwn(node.style, 'verticalAlign')) {
      const align = node.style.verticalAlign;
      if (!['baseline', 'middle', 'text-top', 'text-bottom', 'top', 'bottom', 'sub', 'super'].includes(align)
        && !Number.isFinite(align)
        && !((exactFields(align, ['percent']) || exactFields(align, ['length', 'percent']))
          && Number.isFinite(align.percent) && (!Object.hasOwn(align, 'length') || Number.isFinite(align.length)))) throw new Error(`Invalid node ${index}.style.verticalAlign`);
    }
    for (const [key, values] of [
      ['baselineSource', ['auto', 'first', 'last']], ['textAlign', ['start', 'end', 'left', 'right', 'center', 'justify']],
      ['textAlignLast', ['auto', 'start', 'end', 'left', 'right', 'center', 'justify']],
      ['textJustify', ['auto', 'none', 'inter-word', 'inter-character', 'distribute']],
      ['textOrientation', ['mixed', 'upright', 'sideways']],
      ['unicodeBidi', ['normal', 'embed', 'isolate', 'bidi-override', 'isolate-override', 'plaintext']],
      ['textWrap', ['wrap', 'nowrap']], ['boxDecorationBreak', ['slice', 'clone']],
      ['marginBreak', ['auto', 'keep', 'discard']],
      ['breakBefore', ['auto', 'avoid', 'always', 'all', 'avoid-page', 'page', 'left', 'right', 'recto', 'verso', 'avoid-column', 'column', 'avoid-region', 'region']],
      ['breakAfter', ['auto', 'avoid', 'always', 'all', 'avoid-page', 'page', 'left', 'right', 'recto', 'verso', 'avoid-column', 'column', 'avoid-region', 'region']],
      ['breakInside', ['auto', 'avoid', 'avoid-page', 'avoid-column', 'avoid-region']],
      ['whiteSpaceCollapse', ['collapse', 'discard', 'preserve', 'preserve-breaks', 'preserve-spaces', 'break-spaces']]
    ]) {
      if (Object.hasOwn(node.style, key) && !values.includes(node.style[key])) throw new Error(`Invalid node ${index}.style.${key}`);
    }
    if (Object.hasOwn(node, 'scrollOffset')) scrollOffset(node.scrollOffset, `Node ${index}.scrollOffset`);
    const measured = Object.hasOwn(node, 'measure'), natural = Object.hasOwn(node, 'natural');
    if (measured && natural) throw new Error(`Node ${index} cannot combine measure and natural`);
    if ((measured || natural || node.style.replaced) && input.nodes.some(child => child.parent === index)) throw new Error(`Measured or replaced node ${index} must be a leaf`);
    if (natural) {
      if (!node.natural || typeof node.natural !== 'object' || Array.isArray(node.natural)) throw new Error(`Node ${index}.natural must be an object`);
      for (const [key, value] of Object.entries(node.natural)) {
        if (!['width', 'height', 'aspectRatio'].includes(key) || !Number.isFinite(value) || value < 0) throw new Error(`Invalid node ${index}.natural.${key}`);
      }
    }
    if (Object.hasOwn(node.style, 'aspectRatioAuto') && typeof node.style.aspectRatioAuto !== 'boolean') throw new Error(`Node ${index}.style.aspectRatioAuto must be boolean`);
    for (const [key, values] of [['scrollbarWidth', ['auto', 'thin', 'none']], ['scrollbarGutter', ['auto', 'stable', 'stable both-edges', 'both-edges stable']]]) {
      if (Object.hasOwn(node.style, key) && !values.includes(node.style[key])) throw new Error(`Invalid node ${index}.style.${key}`);
    }
  });
}

function runNative(input) {
  const result = spawnSync(options.cli, [], { input: JSON.stringify(input), encoding: 'utf8', timeout: 30000, maxBuffer: 16 * 1024 * 1024 });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`Native CLI exited ${result.status ?? result.signal}: ${result.stderr.trim()}`);
  let output;
  try { output = JSON.parse(result.stdout); } catch { throw new Error(`Native CLI returned invalid JSON: ${result.stdout.slice(0, 300)}`); }
  if (!Array.isArray(output.sources) || output.sources.length !== input.nodes.length) throw new Error('Native CLI returned the wrong source count');
  if (output.schemaVersion !== 3 || !Array.isArray(output.boxes) || !Array.isArray(output.fragments) || !Array.isArray(output.texts)) throw new Error('Native CLI requires schema v3 generated boxes, fragments, and texts');
  const indexValue = (value, count) => value === null || Number.isInteger(value) && value >= 0 && value < count;
  const rectangle = (rect, path) => {
    for (const key of rectangleFields) {
      if (!Number.isFinite(rect?.[key]) || (key === 'width' || key === 'height') && rect[key] < 0) throw new Error(`Invalid native ${path}.${key}`);
    }
  };
  const baselineSets = (box, path) => {
    for (const key of ['firstBaselines', 'lastBaselines']) {
      const set = box[key];
      if (!set || typeof set !== 'object' || Array.isArray(set)
        || Object.keys(set).length !== baselineFields.length
        || baselineFields.some(field => !Object.hasOwn(set, field) || !Number.isFinite(set[field]))) throw new Error(`Invalid native ${path}.${key}`);
    }
    if (Object.hasOwn(box, 'firstBaseline') || Object.hasOwn(box, 'lastBaseline')) throw new Error(`Invalid native ${path} scalar baselines`);
  };
  const used = (box, path, fields = usedFields) => {
    for (const key of fields) {
      if (booleanFields.includes(key) ? typeof box[key] !== 'boolean' : !Number.isFinite(box[key])) throw new Error(`Invalid native ${path}.${key}`);
    }
    if (box.scrollableOverflowWidth < 0 || box.scrollableOverflowHeight < 0 || gutterFields.some(key => box[key] < 0)) throw new Error(`Invalid native ${path} used dimensions`);
  };
  output.boxes.forEach((box, index) => {
    used(box, `boxes[${index}]`);
    baselineSets(box, `boxes[${index}]`);
    if (!['block', 'flex', 'grid', 'inline', 'inline-formatting', 'text'].includes(box.kind) || !indexValue(box.source, input.nodes.length)) throw new Error(`Invalid native boxes[${index}] metadata`);
    for (const key of ['parent', 'firstChild', 'lastChild', 'nextSibling', 'previousContinuation', 'nextContinuation']) {
      if (!indexValue(box[key], output.boxes.length)) throw new Error(`Invalid native boxes[${index}].${key}`);
    }
    for (const key of ['firstFragment', 'lastFragment']) {
      if (!indexValue(box[key], output.fragments.length)) throw new Error(`Invalid native boxes[${index}].${key}`);
    }
  });
  output.fragments.forEach((fragment, index) => {
    const path = `fragments[${index}]`;
    if (!['box', 'inline', 'text', 'line'].includes(fragment.kind) || !indexValue(fragment.source, input.nodes.length)
      || !indexValue(fragment.box, output.boxes.length)) throw new Error(`Invalid native ${path} metadata`);
    for (const key of ['parent', 'firstChild', 'lastChild', 'previousSibling', 'nextSibling', 'previousForBox', 'nextForBox']) {
      if (!indexValue(fragment[key], output.fragments.length)) throw new Error(`Invalid native ${path}.${key}`);
    }
    for (const key of ['location', 'visualOffset', 'worldLocation']) {
      if (!Number.isFinite(fragment[key]?.x) || !Number.isFinite(fragment[key]?.y)) throw new Error(`Invalid native ${path}.${key}`);
    }
    rectangle({ x: 0, y: 0, ...fragment.size }, `${path}.size`);
    for (const key of ['contentRect', 'paddingRect', 'layoutBounds', 'scrollableOverflow']) rectangle(fragment[key], `${path}.${key}`);
    baselineSets(fragment, path);
    if (!Number.isInteger(fragment.decorationEdges) || fragment.decorationEdges < 0 || fragment.decorationEdges > 15
      || !Number.isInteger(fragment.fragmentainer) || fragment.fragmentainer < 0
      || !indexValue(fragment.textData, output.texts.length)) throw new Error(`Invalid native ${path} text or decoration metadata`);
    if (input.fragmentation && !input.fragmentation.repeatStep
      && fragment.fragmentainer >= input.fragmentation.frames.length) throw new Error(`Native ${path} exceeds the finite frame sequence`);
    if (fragment.kind === 'text') {
      const text = output.texts[fragment.textData], source = input.nodes[fragment.source];
      if (!text || typeof source?.text !== 'string' || output.boxes[fragment.box]?.kind !== 'text'
        || !Number.isInteger(text.sourceStart) || text.sourceStart < 0
        || !Number.isInteger(text.sourceEnd) || text.sourceEnd <= text.sourceStart || text.sourceEnd > Buffer.byteLength(source.text)
        || !Number.isInteger(text.fontKey) || text.fontKey < 0 || text.fontKey > (input.fonts?.length ?? 0)
        || !Number.isInteger(text.bidiLevel) || text.bidiLevel < 0 || text.bidiLevel > 126
        || !Number.isInteger(text.shapeKey) || text.shapeKey < 0) throw new Error(`Invalid native ${path} text data`);
    } else if (fragment.textData !== null) throw new Error(`Non-text native ${path} owns text data`);
    for (const key of ['hasFirstBaseline', 'hasLastBaseline', 'first', 'last', 'phantom', 'nonpainting', 'repeated', 'overflowOnly']) {
      if (typeof fragment[key] !== 'boolean') throw new Error(`Invalid native ${path}.${key}`);
    }
  });
  output.sources.forEach((source, index) => {
    const path = `sources[${index}]`;
    const text = Object.hasOwn(input.nodes[index], 'text');
    if (source.kind !== (text ? 'text' : 'element') || (text ? source.text !== input.nodes[index].text : Object.hasOwn(source, 'text'))) throw new Error(`Invalid native ${path} source content`);
    rectangle(source, path);
    if (!indexValue(source.firstBox, output.boxes.length) || !Array.isArray(source.rects)) throw new Error(`Invalid native ${path} mapping`);
    source.rects.forEach((rect, i) => rectangle(rect, `${path}.rects[${i}]`));
    const box = output.boxes[source.firstBox];
    if (box && box.source !== index) throw new Error(`Invalid native ${path}.firstBox source`);
    const ordinary = box && ['block', 'flex', 'grid'].includes(box.kind) && box.nextContinuation === null
      && source.rects.length === 1 && output.fragments[box.firstFragment]?.kind === 'box';
    if (source.firstBox === null || ordinary) used(source, path, sourceFields);
    else if (sourceFields.filter(field => !rectangleFields.includes(field)).some(field => Object.hasOwn(source, field))) throw new Error(`Native ${path} fabricates single-box metrics`);
  });
  for (const field of rectangleFields) {
    if (!Number.isFinite(output.viewportOverflow?.[field]) || (['width', 'height'].includes(field) && output.viewportOverflow[field] < 0)) throw new Error(`Invalid native viewportOverflow.${field}`);
  }
  return output;
}

// These are adapter limitations, never a list of known engine failures.
function unsupportedAdapter(input, output, fontEvidence = []) {
  const reasons = [];
  const columns = columnGeometry(input);
  if (columns?.unsupported) reasons.push({ node: null, feature: 'fragmentation-geometry', reason: columns.unsupported });
  // Fonts participate through IFC struts, inline boxes, and atomic inline
  // children (whose percentage vertical-align can resolve against line-height).
  const fontOwners = new Set(output.boxes.filter(box => {
    if (['inline', 'inline-formatting', 'text'].includes(box.kind)) return true;
    const style = input.nodes[box.source]?.style;
    return ['inline', 'inline-formatting'].includes(output.boxes[box.parent]?.kind)
      && style && !['absolute', 'fixed'].includes(style.position)
      && (style.float ?? 'none') === 'none'
      && style.verticalAlign && typeof style.verticalAlign === 'object'
      && Object.hasOwn(style.verticalAlign, 'percent');
  }).map(box => box.styleOwner));
  input.nodes.forEach((node, index) => {
    if (Object.hasOwn(node, 'text')) return;
    const key = node.style.fontKey ?? 0;
    const font = key === 0 ? null : input.fonts[key - 1];
    const evidence = fontEvidence.find(value => value.key === key);
    if (fontOwners.has(index) && font && [...Object.values(font).filter(value => typeof value === 'number'), ...Object.values(font.baselines)].some(value => value !== 0)
      && (!evidence?.matched || (node.style.writingMode ?? 'horizontal-tb') !== 'horizontal-tb')) {
      reasons.push({ node: index, feature: 'fixture-font-metrics', reason: evidence?.reason ?? 'The exercised fixture font metrics have no corresponding measured horizontal browser font.', fontKey: key });
    }
    if (fontOwners.has(index) && evidence?.matched && (node.style.lineHeight ?? 'normal') === 'normal'
      && evidence?.metricMatches?.normalLineHeight !== true) {
      reasons.push({ node: index, feature: 'fixture-font-line-height', fontKey: key,
        reason: 'The browser font’s measured normal line height does not match the supplied font metrics.' });
    }
    if (fontOwners.has(index) && node.parent !== null) {
      const parentKey = input.nodes[node.parent].style?.fontKey ?? 0;
      const parentEvidence = fontEvidence.find(value => value.key === parentKey);
      const metric = { middle: 'xHeight', sub: 'subscriptOffset', super: 'superscriptOffset' }[node.style.verticalAlign];
      if (metric && fontEvidence.length && parentEvidence?.metricMatches?.[metric] !== true) {
        reasons.push({ node: index, feature: 'fixture-font-alignment', fontKey: parentKey,
          reason: `The browser font's measured ${metric} cannot represent the supplied parent font metric.` });
      }
    }
    if (node.style.replaced && node.measure) reasons.push({ node: index, feature: 'replaced-measure', reason: 'Text measurement metadata cannot be represented as an actual replaced resource; use natural for resource dimensions.' });
    if (node.natural) {
      const { width, height, aspectRatio = 0 } = node.natural;
      // SVG 2 §8.12: two absolute dimensions determine the natural ratio,
      // ahead of viewBox. Do not silently change an independent input triple.
      if (width !== undefined && height !== undefined) {
        const derived = width > 0 && height > 0 ? width / height : 0;
        if (derived !== aspectRatio) reasons.push({ node: index, feature: 'natural-resource-triple',
          reason: 'A data-SVG with both natural dimensions derives its ratio from width/height (zero dimensions give no ratio). It cannot represent an absent, zero, or conflicting independent ratio. Supply an exactly matching positive ratio, or use a normative native fixture for this triple.',
          natural: node.natural, svgAspectRatio: derived });
      }
    }
    if (node.measure) {
      const m = node.measure;
      if (m.minWidth !== m.maxWidth || m.wrap) reasons.push({ node: index, feature: 'variable-intrinsic-measure', reason: 'The DOM adapter is an atomic fixed intrinsic box, not a text shaping or wrapping provider.' });
      const first = m.firstBaseline ?? m.lastBaseline ?? m.height, last = m.lastBaseline ?? first;
      if (first !== last && (first < 0 || last < 0 || first > m.height || last > m.height)) reasons.push({ node: index, feature: 'out-of-box-measure-baselines', reason: 'Distinct fixed-box metric lines require both baselines within the supplied height.' });
      if ((node.style.writingMode ?? 'horizontal-tb') !== 'horizontal-tb' && (m.firstBaseline !== undefined || m.lastBaseline !== undefined)) reasons.push({ node: index, feature: 'nonhorizontal-measure-baseline', reason: 'The synthetic baseline marker represents horizontal-tb only; vertical and sideways baseline measurements require a matching provider.' });
      if (node.style.display && !['block', 'inline-block'].includes(node.style.display)) reasons.push({ node: index, feature: 'measured-formatting-context', reason: 'Measured DOM leaves use a block or inline-block inline formatting context.' });
    }
  });
  return reasons;
}

async function findChrome() {
  if (options.chrome) { await access(options.chrome, constants.X_OK); return resolve(options.chrome); }
  const candidates = process.platform === 'darwin'
    ? ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/Applications/Chromium.app/Contents/MacOS/Chromium']
    : ['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', '/usr/bin/chromium-browser'];
  for (const path of candidates) {
    try { await access(path, constants.X_OK); return path; } catch { /* Try the next known application path. */ }
  }
  throw new Error('Chrome not found; supply --chrome PATH or CHROME_PATH. No browser is downloaded automatically.');
}

class Chrome {
  constructor(process, profile, executable) {
    this.process = process; this.profile = profile; this.executable = executable;
    this.nextId = 0; this.pending = new Map(); this.buffer = Buffer.alloc(0); this.stderr = '';
    process.stderr.on('data', chunk => { this.stderr = (this.stderr + chunk).slice(-8000); });
    process.stdio[4].on('data', chunk => {
      this.buffer = Buffer.concat([this.buffer, chunk]);
      for (let end; (end = this.buffer.indexOf(0)) !== -1;) {
        const data = this.buffer.subarray(0, end).toString(); this.buffer = this.buffer.subarray(end + 1);
        if (!data) continue;
        let message;
        try { message = JSON.parse(data); } catch { this.fail(new Error(`Invalid CDP message: ${data.slice(0, 100)}`)); continue; }
        const pending = this.pending.get(message.id);
        if (!pending) continue;
        this.pending.delete(message.id); clearTimeout(pending.timer);
        if (message.error) pending.reject(new Error(JSON.stringify(message.error)));
        else pending.resolve(message.result);
      }
    });
    process.on('error', error => this.fail(error));
    process.on('exit', (code, signal) => this.fail(new Error(`Chrome exited ${code ?? signal}: ${this.stderr}`)));
    process.stdio[3].on('error', error => this.fail(error));
  }
  fail(error) {
    for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(error); }
    this.pending.clear();
  }
  send(method, params = {}, sessionId) {
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`CDP timeout: ${method}; ${this.stderr}`)); }, 15000);
      this.pending.set(id, { resolve, reject, timer });
      this.process.stdio[3].write(`${JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) })}\0`);
    });
  }
  async close() {
    if (this.process.exitCode === null && this.process.signalCode === null) {
      const exited = new Promise(resolve => this.process.once('exit', resolve));
      this.process.kill('SIGTERM');
      const force = setTimeout(() => this.process.kill('SIGKILL'), 2000);
      await exited; clearTimeout(force);
    }
    await rm(this.profile, { recursive: true, force: true });
  }
}

async function startChrome() {
  const executable = await findChrome();
  const profile = await mkdtemp(join(tmpdir(), 'silk-layout-chrome-'));
  const child = spawn(executable, ['--headless=new', '--remote-debugging-pipe', `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--disable-background-networking', '--disable-component-update',
    '--disable-extensions', '--force-device-scale-factor=1', 'about:blank'],
  { stdio: ['ignore', 'ignore', 'pipe', 'pipe', 'pipe'] });
  browser = new Chrome(child, profile, executable);
  const version = await browser.send('Browser.getVersion');
  summary.browser = { executable, ...version, deviceScaleFactor: 1, locale: 'en-US' };
  if (options.chromeVersion && version.product !== options.chromeVersion) throw new Error(`Chrome version mismatch: expected ${options.chromeVersion}, got ${version.product}`);
  summary.browser.replayVersionArgument = ['--chrome-version', version.product];
  const { targetId } = await browser.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await browser.send('Target.attachToTarget', { targetId, flatten: true });
  browser.sessionId = sessionId;
  await browser.send('Emulation.setLocaleOverride', { locale: 'en-US' }, sessionId);
  await browser.send('Emulation.setTimezoneOverride', { timezoneId: 'UTC' }, sessionId);
}

// This function is serialized and executed in the isolated blank Chrome page.
async function observeDOM(input, fontAssets, columns) {
  // A fresh about:blank document is in quirks mode. CSS layout comparisons
  // require standards mode, particularly for zero-font inline struts.
  if (document.compatMode !== 'CSS1Compat') {
    document.open();
    document.write('<!doctype html><html><head></head><body></body></html>');
    document.close();
  }
  const dimensions = new Set(['width', 'height', 'minWidth', 'minHeight', 'maxWidth', 'maxHeight', 'flexBasis', 'rowGap', 'columnGap']);
  const sides = ['top', 'right', 'bottom', 'left'];
  const defaults = { display: 'block', boxSizing: 'content-box', direction: 'ltr', unicodeBidi: 'normal', writingMode: 'horizontal-tb', textOrientation: 'mixed',
    flexDirection: 'row', flexWrap: 'nowrap', flexGrow: 0, flexShrink: 1, flexBasis: 'auto', order: 0,
    gridTemplateRows: 'none', gridTemplateColumns: 'none', gridTemplateAreas: 'none',
    gridAutoRows: 'auto', gridAutoColumns: 'auto', gridAutoFlow: 'row',
    rowGap: 'normal', columnGap: 'normal',
    gridRowStart: 'auto', gridRowEnd: 'auto', gridColumnStart: 'auto', gridColumnEnd: 'auto',
    justifyContent: 'normal', justifySelf: 'auto', justifyItems: 'normal', alignItems: 'normal', alignSelf: 'auto', alignContent: 'normal',
    overflowX: 'visible', overflowY: 'visible', scrollbarWidth: 'auto', scrollbarGutter: 'auto',
    position: 'static', float: 'none', clear: 'none', visibility: 'visible',
    lineHeight: 'normal', verticalAlign: 'baseline', baselineSource: 'auto',
    breakBefore: 'auto', breakAfter: 'auto', breakInside: 'auto', orphans: 2, widows: 2,
    textAlign: 'start', textAlignLast: 'auto', textIndent: '0px', textWrap: 'wrap', whiteSpaceCollapse: 'collapse', tabSize: 8, boxDecorationBreak: 'slice' };
  const dimension = value => {
    if (typeof value === 'number') return `${value}px`;
    if (typeof value === 'string') return value;
    if (value && typeof value === 'object' && typeof value.percent === 'number') {
      return value.length === undefined ? `${value.percent * 100}%` : `calc(${value.length}px + ${value.percent * 100}%)`;
    }
    throw new Error(`Invalid dimension ${JSON.stringify(value)}`);
  };
  const gridTrack = value => {
    if (value && typeof value === 'object') {
      if (Object.hasOwn(value, 'fr')) return `${value.fr}fr`;
      if (Object.hasOwn(value, 'minmax')) return `minmax(${gridTrack(value.minmax[0])}, ${gridTrack(value.minmax[1])})`;
      if (Object.hasOwn(value, 'fitContent')) return `fit-content(${dimension(value.fitContent)})`;
    }
    return dimension(value);
  };
  // Subgrid line-name groups are slots: preserve adjacent and empty groups.
  const subgridNames = values => values.map(value =>
    Object.hasOwn(value, 'repeat')
      ? `repeat(${value.repeat.count}, ${subgridNames(value.repeat.tracks)})`
      : `[${value.names.map(name => CSS.escape(name)).join(' ')}]`).join(' ');
  const gridTracks = values => {
    if (values && typeof values === 'object' && Object.hasOwn(values, 'subgrid')) {
      const names = subgridNames(values.subgrid);
      return names ? `subgrid ${names}` : 'subgrid';
    }
    if (!Array.isArray(values)) return gridTrack(values);
    const parts = [];
    let names = [];
    const flushNames = () => {
      if (names.length) parts.push(`[${names.map(name => CSS.escape(name)).join(' ')}]`);
      names = [];
    };
    for (const value of values) {
      if (value && typeof value === 'object' && Object.hasOwn(value, 'names')) {
        names.push(...value.names);
        continue;
      }
      flushNames();
      if (value && typeof value === 'object' && Object.hasOwn(value, 'repeat')) {
        parts.push(`repeat(${value.repeat.count}, ${gridTracks(value.repeat.tracks)})`);
      } else parts.push(gridTrack(value));
    }
    flushNames();
    return parts.join(' ');
  };
  const gridLine = value => {
    if (typeof value === 'number') return String(value);
    if (typeof value === 'string') return value === 'auto' ? value : CSS.escape(value);
    const name = value.name === undefined ? '' : ` ${CSS.escape(value.name)}`;
    return Object.hasOwn(value, 'span') ? `span ${value.span}${name}` : `${value.line}${name}`;
  };
  const gridValue = (name, value) => {
    if (/^grid(?:Template|Auto)(Rows|Columns)$/.test(name)) return gridTracks(value);
    if (name === 'gridTemplateAreas') return value.length ? value.map(row => JSON.stringify(row.join(' '))).join(' ') : 'none';
    if (/^grid(Row|Column)(Start|End)$/.test(name)) return gridLine(value);
    return value;
  };
  const gridProperties = Object.keys(defaults).filter(name => name.startsWith('grid'));
  const textIndentValue = value => {
    const options = value && typeof value === 'object' && Object.hasOwn(value, 'value') ? value : { value };
    return `${dimension(options.value)}${options.hanging ? ' hanging' : ''}${options.eachLine ? ' each-line' : ''}`;
  };
  const cssName = name => name.replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`);
  const set = (element, name, value) => {
    const property = cssName(name);
    if (!CSS.supports(property, String(value))) throw new Error(`Chrome rejects ${property}: ${value}`);
    element.style.setProperty(property, String(value));
  };
  document.documentElement.style.cssText = 'margin:0;padding:0;border:0;width:100%;height:100%;overflow:hidden;';
  document.body.replaceChildren();
  document.body.style.cssText = 'display:flow-root;margin:0;padding:0;border:0;width:100%;height:100%;overflow:hidden;font-size:0;line-height:0;';
  const fontEvidence = [], textEvidence = [], textJustifyEvidence = [], textIndentEvidence = [], marginBreakEvidence = [], gridEvidence = [];
  const zeroFont = { fontSize: 0, ascent: 0, descent: 0, lineGap: 0, xHeight: 0,
    capHeight: 0, subscriptOffset: 0, superscriptOffset: 0 };
  const fontMetrics = key => key === 0 ? zeroFont : input.fonts[key - 1];
  const assets = new Map((fontAssets?.fonts ?? []).map(font => [font.key, font]));
  const fontFamily = key => assets.get(key)?.family ?? 'monospace';
  if (fontAssets) {
    document.fonts.clear();
    const keys = new Set(input.nodes.filter(node => node.style).map(node => node.style.fontKey ?? 0));
    const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 ';
    for (const key of keys) {
      const metrics = fontMetrics(key), asset = assets.get(key);
      const evidence = { key, family: fontFamily(key), sha256: asset?.sha256 ?? null,
        fontSize: metrics.fontSize, expected: metrics, matched: false };
      fontEvidence.push(evidence);
      const unavailable = fontAssets.unsupported.find(font => font.key === key);
      if (unavailable || key !== 0 && !asset) {
        evidence.reason = unavailable?.reason ?? 'The authored font backend supplied no matching asset.';
        continue;
      }
      if (metrics.fontSize !== 0) {
        try {
          if (!asset.dataUrl) throw new Error('Missing nonzero font data');
          const face = new FontFace(asset.family, `url("${asset.dataUrl}")`, { style: 'normal', weight: '400' });
          await face.load();
          document.fonts.add(face);
          await document.fonts.load(`${metrics.fontSize}px "${asset.family}"`, alphabet);
          evidence.loadStatus = face.status;
          if (face.status !== 'loaded' || !document.fonts.check(`${metrics.fontSize}px "${asset.family}"`, alphabet)) {
            throw new Error('FontFace did not become available');
          }
        } catch (error) {
          evidence.reason = `Authored font could not be loaded in Chrome: ${error.message}`;
          continue;
        }
      } else evidence.loadStatus = 'zero-size-no-asset';
      // Isolated calibration uses only the supplied font profile, never native
      // layout output or the geometry of a fixture under comparison.
      const probe = document.createElement('div');
      probe.style.cssText = `position:absolute;left:0;top:0;visibility:hidden;white-space:pre;direction:ltr;writing-mode:horizontal-tb;font-family:"${fontFamily(key)}";font-size:${metrics.fontSize}px;font-weight:400;font-style:normal;font-synthesis:none;font-kerning:none;font-variant-ligatures:none;letter-spacing:0;word-spacing:0;line-height:normal;padding:0;border:0;margin:0;`;
      const sample = document.createTextNode(alphabet);
      const baseline = document.createElement('span');
      baseline.style.cssText = 'display:inline-block;vertical-align:baseline;width:0;height:0;margin:0;padding:0;border:0;';
      probe.append(sample, baseline);
      document.body.append(probe);
      const range = document.createRange();
      range.selectNodeContents(sample);
      const bounds = range.getBoundingClientRect(), baselineY = baseline.getBoundingClientRect().bottom;
      const advances = [...alphabet].map((character, offset) => {
        range.setStart(sample, offset); range.setEnd(sample, offset + 1);
        return { character, width: range.getBoundingClientRect().width };
      });
      const units = document.createElement('span');
      units.style.cssText = 'position:absolute;width:1ex;height:1cap;padding:0;border:0;margin:0;';
      probe.append(units);
      const unitBounds = units.getBoundingClientRect();
      const scriptShift = value => {
        const marker = baseline.cloneNode();
        marker.style.verticalAlign = value;
        probe.append(marker);
        const shift = marker.getBoundingClientRect().bottom - baseline.getBoundingClientRect().bottom;
        marker.remove();
        return shift;
      };
      const actual = { ascent: baselineY - bounds.top, descent: bounds.bottom - baselineY,
        xHeight: unitBounds.width, capHeight: unitBounds.height,
        subscriptOffset: scriptShift('sub'), superscriptOffset: -scriptShift('super'),
        normalLineHeight: probe.getBoundingClientRect().height, advances };
      probe.remove();
      const near = (a, b) => Number.isFinite(a) && Math.abs(a - b) <= 1e-6;
      evidence.actual = actual;
      evidence.metricMatches = Object.fromEntries(['ascent', 'descent', 'xHeight', 'capHeight', 'subscriptOffset', 'superscriptOffset']
        .map(name => [name, near(actual[name], metrics[name])]));
      evidence.metricMatches.normalLineHeight = near(actual.normalLineHeight, metrics.ascent + metrics.descent + metrics.lineGap);
      evidence.matched = evidence.metricMatches.ascent && evidence.metricMatches.descent
        && advances.every(advance => near(advance.width, metrics.fontSize / 2));
      if (!evidence.matched) evidence.reason = 'The loaded browser font does not reproduce the supplied ascent/descent and ASCII half-em advances in isolated DOM Range calibration.';
    }
  }
  document.getElementById('silk-scrollbar-policy')?.remove();
  const scrollbarCSS = document.createElement('style');
  scrollbarCSS.id = 'silk-scrollbar-policy';
  // A zero-size pseudo scrollbar retains scrollability and the original overlay
  // corpus geometry. Do not use scrollbar-width:none: that changes the input CSS.
  scrollbarCSS.textContent = input.nodes.map((node, i) => {
    if (Object.hasOwn(node, 'text')) return '';
    const metrics = input.scrollbars?.[node.style.scrollbarWidth ?? 'auto'];
    const horizontal = metrics && !metrics.overlay ? metrics.horizontal : 0;
    const vertical = metrics && !metrics.overlay ? metrics.vertical : 0;
    return `[data-node="${i}"]::-webkit-scrollbar{width:${vertical}px;height:${horizontal}px}`;
  }).join('\n');
  document.head.append(scrollbarCSS);
  window.scrollTo(0, 0);
  // This subtree boundary is the native viewport, not the HTML root scrollport.
  // Positioning establishes the abspos CB without trapping viewport-fixed boxes.
  const viewport = document.createElement('div');
  viewport.style.cssText = `position:relative;display:flow-root;box-sizing:content-box;margin:0;padding:0;border:0;width:${input.viewport.width}px;height:${input.viewport.height}px;overflow:hidden;direction:ltr;writing-mode:horizontal-tb;overflow-anchor:none;`;
  document.body.append(viewport);
  let flow = viewport;
  if (columns) {
    flow = document.createElement('div');
    flow.style.cssText = `position:relative;left:${columns.x}px;top:${columns.y}px;box-sizing:content-box;margin:0;padding:0;border:0;width:${columns.width}px;height:${columns.height}px;writing-mode:${columns.mode};direction:${columns.direction};column-count:1;column-width:auto;column-gap:${columns.gap}px;column-fill:auto;overflow:visible;`;
    viewport.append(flow);
  }
  const elements = [], markers = [], lastMarkers = [], loads = [], resources = [];
  for (let i = 0; i < input.nodes.length; i++) {
    const node = input.nodes[i];
    if (Object.hasOwn(node, 'text')) {
      const text = document.createTextNode(node.text);
      elements[node.parent].append(text);
      elements.push(text);
      continue;
    }
    const replaced = node.natural !== undefined || node.style.replaced;
    const element = document.createElement(replaced ? 'img' : 'div');
    if (replaced) {
      const natural = node.natural ?? {}, attributes = [];
      // Missing dimensions stay missing; zero remains an explicit length.
      // SVG intrinsic sizing: https://www.w3.org/TR/SVG2/coords.html#IntrinsicSizing
      for (const name of ['width', 'height']) {
        if (natural[name] !== undefined) attributes.push(`${name}="${natural[name]}px"`);
      }
      if (natural.aspectRatio > 0) attributes.push(`viewBox="0 0 ${natural.aspectRatio} 1"`);
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" ${attributes.join(' ')}></svg>`;
      resources.push({ node: i, kind: 'data-svg-image', natural, svg });
      // Register before assigning src, including when the same data URL is cached.
      const loaded = new Promise((resolve, reject) => {
        element.onload = resolve;
        element.onerror = () => reject(new Error(`Resource node ${i} failed to load`));
      });
      element.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
      loads.push(loaded.then(async () => {
        await element.decode();
        element.onload = element.onerror = null;
      }));
    }
    element.dataset.node = String(i);
    element.style.overflowAnchor = 'none';
    for (const [name, value] of Object.entries(defaults)) set(element, name, value);
    // Native element styles are computed values. Reset this inherited property
    // per element, but do not reject default fixtures on browsers without it.
    element.style.setProperty('text-justify', 'auto');
    element.style.setProperty('margin-break', 'auto');
    if (fontAssets) {
      // Element styles are already computed in the native protocol: omitted
      // fontKey means zero, not inherited. DOM Text inherits its owning element.
      const key = node.style.fontKey ?? 0, metrics = fontMetrics(key);
      const height = node.style.lineHeight ?? 'normal';
      const lineHeight = height === 'normal' ? metrics.ascent + metrics.descent + metrics.lineGap
        : typeof height === 'number' ? metrics.fontSize * height : height.length;
      set(element, 'fontFamily', `"${fontFamily(key)}"`);
      set(element, 'fontSize', `${metrics.fontSize}px`);
      set(element, 'lineHeight', height === 'normal' ? 'normal' : `${lineHeight}px`);
      set(element, 'fontSynthesis', 'none');
      set(element, 'fontKerning', 'none');
      set(element, 'fontVariantLigatures', 'none');
    }
    // Individual sides override shorthands regardless of JSON property order.
    const edges = new Set(['padding', 'margin', 'border', 'inset']);
    const entries = Object.entries(node.style).sort(([a], [b]) => Number(edges.has(b)) - Number(edges.has(a)));
    for (const [name, value] of entries) {
      if (name.endsWith('Safety') || name === 'replaced' || name === 'aspectRatioAuto' || name === 'fontKey') continue;
      if (gridProperties.includes(name)) {
        // Keep support and computed-value evidence even when Chrome rejects a
        // declaration. The native adapter validates the structured input.
        element.style.setProperty(cssName(name), gridValue(name, value));
        continue;
      }
      if (name === 'textJustify') {
        // Retain the requested spelling, including the mandated legacy alias.
        // Unsupported declarations are reported with computed-style evidence.
        element.style.setProperty('text-justify', value);
        continue;
      }
      if (name === 'marginBreak') {
        element.style.setProperty('margin-break', value);
        continue;
      }
      if (name === 'textIndent') {
        element.style.setProperty('text-indent', textIndentValue(value));
        continue;
      }
      if (name === 'lineHeight') {
        if (fontAssets) continue;
        set(element, name, typeof value === 'object' ? `${value.length}px` : value);
        continue;
      }
      if (name === 'tabSize') {
        set(element, name, typeof value === 'object' ? `${value.length}px` : value);
        continue;
      }
      if (name === 'verticalAlign') {
        set(element, name, dimension(value));
        continue;
      }
      if (name === 'aspectRatio') {
        set(element, name, value === 0 ? 'auto' : `${node.style.aspectRatioAuto ? 'auto ' : ''}${value}`);
        continue;
      }
      if (['padding', 'margin', 'border', 'inset'].includes(name)) {
        for (const side of sides) {
          const v = typeof value === 'object' && !('percent' in value) ? value[side] : value;
          if (v === undefined) continue;
          const key = name === 'inset' ? side : `${name}${side[0].toUpperCase()}${side.slice(1)}${name === 'border' ? 'Width' : ''}`;
          set(element, key, dimension(v));
          if (name === 'border') set(element, `border${side[0].toUpperCase()}${side.slice(1)}Style`, 'solid');
        }
      } else if (/^(margin|padding|border|inset)(Top|Right|Bottom|Left)$/.test(name)) {
        let property = name;
        if (name.startsWith('inset')) property = name.slice(5).toLowerCase();
        if (name.startsWith('border')) { property += 'Width'; set(element, `${name}Style`, 'solid'); }
        set(element, property, dimension(value));
      } else set(element, name, dimensions.has(name) ? dimension(value) : value);
    }
    for (const property of ['justifyContent', 'justifySelf', 'justifyItems', 'alignContent', 'alignItems', 'alignSelf']) {
      const safety = node.style[`${property}Safety`];
      if (safety && safety !== 'default') set(element, property, `${safety} ${node.style[property] ?? defaults[property]}`);
    }
    // compute() enters an independent formatting context. The DOM wrapper is
    // not the document element, so establish that same boundary explicitly.
    if (!replaced && node.parent === null && ['block', 'flow', 'block flow', 'flow block'].includes(node.style.display ?? 'block')) {
      set(element, 'display', 'flow-root');
    }
    if (node.measure && node.measure.hasLineBoxes !== false) {
      const m = node.measure, atomic = document.createElement('span'), marker = document.createElement('span');
      const baseline = m.firstBaseline ?? m.lastBaseline ?? m.height;
      atomic.style.cssText = `display:inline-block;vertical-align:baseline;width:${m.maxWidth}px;height:${m.height}px;line-height:0;font-size:0;padding:0;border:0;margin:0;`;
      // A zero-width inline box sets the line baseline without platform fonts.
      marker.style.cssText = `display:inline-block;vertical-align:baseline;width:0;height:${baseline}px;padding:0;border:0;margin:0;`;
      atomic.append(marker); markers[i] = marker;
      const last = m.lastBaseline ?? baseline;
      if (last === baseline) element.append(atomic);
      else {
        // Two overlapping metric lines preserve the declared fixed intrinsic
        // box while exposing distinct first/last in-flow baselines to CSS.
        // Keep them as direct block children: wrapping both in one atomic
        // inline would export only that inline's last baseline.
        const firstLine = document.createElement('div'), lastLine = document.createElement('div');
        firstLine.style.cssText = `height:${m.height}px;line-height:0;font-size:0;padding:0;border:0;margin:0;`;
        lastLine.style.cssText = firstLine.style.cssText;
        lastLine.style.marginTop = `${-m.height}px`;
        const lastAtomic = atomic.cloneNode(true), lastMarker = lastAtomic.firstElementChild;
        lastMarker.style.height = `${last}px`;
        firstLine.append(atomic); lastLine.append(lastAtomic);
        element.append(firstLine, lastLine); lastMarkers[i] = lastMarker;
      }
    }
    (node.parent === null ? flow : elements[node.parent]).append(element);
    elements.push(element);
  }
  await Promise.all(loads);
  const scrollStates = [], unsupported = [], scrollbarEvidence = [];
  const scrollable = value => ['auto', 'scroll', 'hidden', 'overlay'].includes(value);
  const applyScroll = (element, node, supplied, enabled) => {
    const requested = supplied ?? { x: 0, y: 0 };
    const effective = { x: enabled.x ? requested.x : 0, y: enabled.y ? requested.y : 0 };
    element.scrollLeft = effective.x;
    element.scrollTop = effective.y;
    scrollStates.push({ node, requested, effective, element });
  };
  applyScroll(viewport, null, input.viewportScrollOffset, { x: true, y: true });
  elements.forEach((element, i) => {
    if (element.nodeType === Node.TEXT_NODE) return;
    const node = input.nodes[i], style = node.style;
    // Use pinned computed-overflow rules, not Chrome's newer clip conversion.
    // Differences in resulting geometry remain genuine differential failures.
    const x = style.overflowX ?? 'visible', y = style.overflowY ?? 'visible';
    const generatesBox = element.getClientRects().length !== 0;
    applyScroll(element, i, node.scrollOffset, {
      x: generatesBox && (scrollable(x) || x === 'visible' && scrollable(y)),
      y: generatesBox && (scrollable(y) || y === 'visible' && scrollable(x))
    });
  });
  await new Promise(resolve => requestAnimationFrame(resolve));
  // Inputs are scroll requests. Range clamping is expected; resulting geometry
  // is compared normally, including any disagreement from offset quantization.
  const scrollOffsets = scrollStates.map(({ element, ...state }) => {
    const actual = { x: element.scrollLeft, y: element.scrollTop };
    return { ...state, actual };
  });
  const viewportOrigin = viewport.getBoundingClientRect();
  const origins = [];
  // CSSOM scrollWidth/scrollHeight are integer dimensions, not fractional
  // overflow rectangles or their signed origins. Do not turn them into
  // expected native overflow fields; those have normative spec assertions.
  return { schemaVersion: 3, documentMode: document.compatMode, resources, scrollOffsets, scrollbarEvidence, fontEvidence, textEvidence, textJustifyEvidence, textIndentEvidence, marginBreakEvidence, gridEvidence, unsupported, sources: elements.map((element, i) => {
    const node = input.nodes[i];
    const parent = node.parent === null ? viewportOrigin : origins[node.parent];
    if (element.nodeType === Node.TEXT_NODE) {
      const range = document.createRange();
      range.selectNodeContents(element);
      const rect = range.getBoundingClientRect(), clientRects = [...range.getClientRects()];
      origins[i] = clientRects.length === 0 ? parent : rect;
      const rects = clientRects.map(value => ({ x: value.x - viewportOrigin.x, y: value.y - viewportOrigin.y, width: value.width, height: value.height }));
      const style = getComputedStyle(element.parentElement);
      const key = input.nodes[node.parent].style.fontKey ?? 0;
      const evidence = fontEvidence.find(font => font.key === key);
      const expectedCollapse = input.nodes[node.parent].style.whiteSpaceCollapse ?? 'collapse';
      const profile = !/[^A-Za-z0-9 \t\r\n\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/.test(node.text)
        && style.writingMode === 'horizontal-tb' && ['ltr', 'rtl'].includes(style.direction)
        && style.whiteSpaceCollapse === expectedCollapse;
      textEvidence.push({ node: i, fontKey: key, family: style.fontFamily, fontSize: style.fontSize,
        lineHeight: style.lineHeight, writingMode: style.writingMode, direction: style.direction,
        whiteSpaceCollapse: style.whiteSpaceCollapse, tabSize: style.tabSize, textWrap: style.textWrap,
        textJustify: style.getPropertyValue('text-justify'), textIndent: style.textIndent,
        profileMatched: profile, fontMatched: evidence?.matched === true });
      if (!profile || !evidence?.matched) unsupported.push({ node: i, feature: 'fixture-text-font',
        reason: !profile ? 'Source text is outside the ASCII letters/digits, Bidi_Control characters, ASCII whitespace, horizontal-tb LTR/RTL font profile, or Chrome did not retain the requested white-space-collapse value.'
          : evidence?.reason ?? 'No matching browser font was measured for this source text.',
        fontKey: key, text: node.text, rects });
      return { kind: 'text', text: node.text, x: clientRects.length ? rect.x - parent.x : 0,
        y: clientRects.length ? rect.y - parent.y : 0, width: rect.width, height: rect.height, rects };
    }
    const rect = element.getBoundingClientRect(), style = getComputedStyle(element);
    if (style.display.includes('grid') || gridProperties.some(name => Object.hasOwn(node.style, name))) {
      for (const name of gridProperties) {
        const property = cssName(name);
        const requested = Object.hasOwn(node.style, name) ? gridValue(name, node.style[name]) : defaults[name];
        const evidence = { node: i, property, requested, specified: element.style.getPropertyValue(property),
          computed: style.getPropertyValue(property), cssSupported: CSS.supports(property, requested) };
        gridEvidence.push(evidence);
        // Track lists expose used pixel sizes through getComputedStyle; those
        // cannot be compared textually with authored fr/minmax/repeat values.
        if (!evidence.cssSupported || !evidence.specified || !evidence.computed) {
          unsupported.push({ node: i, feature: property,
            reason: 'Chrome does not support or did not retain the requested grid declaration.', evidence });
        }
      }
    }
    const requestedJustify = node.style.textJustify ?? 'auto';
    const expectedJustify = requestedJustify === 'distribute' ? 'inter-character' : requestedJustify;
    const justifyEvidence = { node: i, requested: requestedJustify, expected: expectedJustify,
      computed: style.getPropertyValue('text-justify'), cssSupported: CSS.supports('text-justify', requestedJustify) };
    textJustifyEvidence.push(justifyEvidence);
    if (requestedJustify !== 'auto' && (!justifyEvidence.cssSupported || justifyEvidence.computed !== expectedJustify)) {
      unsupported.push({ node: i, feature: 'text-justify',
        reason: 'Chrome does not support or did not retain the requested text-justify method.',
        evidence: justifyEvidence });
    }
    const requestedMarginBreak = node.style.marginBreak ?? 'auto';
    const marginEvidence = { node: i, requested: requestedMarginBreak,
      computed: style.getPropertyValue('margin-break'), cssSupported: CSS.supports('margin-break', requestedMarginBreak) };
    marginBreakEvidence.push(marginEvidence);
    if (requestedMarginBreak !== 'auto' && (!marginEvidence.cssSupported || marginEvidence.computed !== requestedMarginBreak)) {
      unsupported.push({ node: i, feature: 'margin-break',
        reason: 'Chrome does not support or did not retain the requested margin-break policy.',
        evidence: marginEvidence });
    }
    if (Object.hasOwn(node.style, 'textIndent')) {
      const requested = textIndentValue(node.style.textIndent);
      const evidence = { node: i, requested, computed: style.textIndent, cssSupported: CSS.supports('text-indent', requested) };
      textIndentEvidence.push(evidence);
      if (!evidence.cssSupported || !element.style.textIndent
        || ['hanging', 'each-line'].some(keyword => requested.includes(keyword) !== style.textIndent.includes(keyword))) {
        unsupported.push({ node: i, feature: 'text-indent', reason: 'Chrome did not retain the requested indentation.', evidence });
      }
    }
    const clientRects = [...element.getClientRects()];
    origins[i] = clientRects.length === 0 ? parent : rect;
    const rects = clientRects.map(value => ({ x: value.x - viewportOrigin.x, y: value.y - viewportOrigin.y, width: value.width, height: value.height }));
    if (clientRects.length === 0) return { kind: 'element', x: 0, y: 0, width: 0, height: 0, contentWidth: 0, contentHeight: 0, rects };
    const px = name => parseFloat(style[name]) || 0;
    const box = { kind: 'element', x: rect.x - parent.x, y: rect.y - parent.y, width: rect.width, height: rect.height, rects };
    // Non-replaced inline content has fragment areas, not one used content box.
    if (clientRects.length !== 1 || style.display === 'inline' && !(element instanceof HTMLImageElement)) return box;
    box.contentWidth = Math.max(0, rect.width - px('paddingLeft') - px('paddingRight') - px('borderLeftWidth') - px('borderRightWidth'));
    box.contentHeight = Math.max(0, rect.height - px('paddingTop') - px('paddingBottom') - px('borderTopWidth') - px('borderBottomWidth'));
    const width = node.style.scrollbarWidth ?? 'auto';
    const metrics = input.scrollbars?.[width];
    const requestedObservation = input.scrollbars !== undefined || node.style.scrollbarWidth !== undefined || node.style.scrollbarGutter !== undefined;
    if (requestedObservation) {
      const evidence = { node: i, metrics: metrics ?? null, scrollbarWidth: style.scrollbarWidth,
        scrollbarGutter: style.scrollbarGutter, overflowX: style.overflowX, overflowY: style.overflowY,
        writingMode: style.writingMode, replaced: element instanceof HTMLImageElement,
        x: rect.x, y: rect.y, width: rect.width, height: rect.height,
        allottedWidth: box.contentWidth, allottedHeight: box.contentHeight,
        border: { top: px('borderTopWidth'), right: px('borderRightWidth'), bottom: px('borderBottomWidth'), left: px('borderLeftWidth') },
        padding: { top: px('paddingTop'), right: px('paddingRight'), bottom: px('paddingBottom'), left: px('paddingLeft') },
        clientWidth: element.clientWidth, clientHeight: element.clientHeight,
        clientLeft: element.clientLeft, clientTop: element.clientTop,
        scrollWidth: element.scrollWidth, scrollHeight: element.scrollHeight,
        computedWidth: style.width, computedHeight: style.height };
      if (style.scrollbarGutter.includes('both-edges')) {
        evidence.childOrigins = [...element.children].map(child => {
          const childRect = child.getBoundingClientRect();
          return { x: childRect.x - rect.x, y: childRect.y - rect.y };
        });
      }
      // Extents alone do not establish actual scrollability (notably Chrome
      // stable-gutter feedback). Restore used offsets before measuring quads.
      const savedX = element.scrollLeft, savedY = element.scrollTop;
      element.scrollLeft = 1e9;
      const positiveX = element.scrollLeft;
      element.scrollLeft = -1e9;
      const negativeX = element.scrollLeft;
      element.scrollTop = 1e9;
      const positiveY = element.scrollTop;
      element.scrollTop = -1e9;
      const negativeY = element.scrollTop;
      element.scrollLeft = savedX; element.scrollTop = savedY;
      evidence.scrollRange = { minX: negativeX, maxX: positiveX, minY: negativeY, maxY: positiveY };
      scrollbarEvidence.push(evidence);
    }
    if (markers[i] && (node.measure.firstBaseline !== undefined || node.measure.lastBaseline !== undefined)) {
      box.firstBaseline = markers[i].getBoundingClientRect().bottom - rect.top;
      box.lastBaseline = (lastMarkers[i] ?? markers[i]).getBoundingClientRect().bottom - rect.top;
      box.hasFirstBaseline = true; box.hasLastBaseline = true;
    }
    return box;
  }) };
}

async function resolveScrollbarGeometry(observation) {
  const scrollable = value => ['auto', 'scroll', 'hidden'].includes(value);
  const group = 'silk-scrollbar-oracle';
  let retainedObjects = false;
  try {
    for (const evidence of observation.scrollbarEvidence) {
      const { metrics, scrollbarWidth: width, scrollbarGutter: gutter, overflowX, overflowY } = evidence;
      const box = observation.sources[evidence.node];
      const horizontalInline = evidence.writingMode === 'horizontal-tb';
      const stable = gutter.includes('stable'), both = gutter.includes('both-edges');
      const classic = metrics && !metrics.overlay && width !== 'none'
        && ([overflowX, overflowY].some(value => value === 'scroll' || value === 'auto')
          || stable && (scrollable(overflowX) || scrollable(overflowY)));
      let left = 0, right = 0, top = 0, bottom = 0;
      if (classic) {
        const remote = await browser.send('Runtime.evaluate', {
          expression: `document.querySelector('[data-node="${evidence.node}"]')`, objectGroup: group
        }, browser.sessionId);
        retainedObjects = true;
        if (!remote.result.objectId) throw new Error(`Scrollbar node ${evidence.node} is no longer available`);
        const { model } = await browser.send('DOM.getBoxModel', { objectId: remote.result.objectId }, browser.sessionId);
        evidence.boxModel = model;
        // CDP's width/height and CSSOM client dimensions are rounded. Only the
        // physical content quad preserves fractional layout coordinates.
        const content = model.content, b = evidence.border, p = evidence.padding;
        left = content[0] - evidence.x - b.left - p.left;
        top = content[1] - evidence.y - b.top - p.top;
        right = evidence.x + evidence.width - b.right - p.right - content[2];
        bottom = evidence.y + evidence.height - b.bottom - p.bottom - content[5];
        box.contentWidth = Math.max(0, content[2] - content[0]);
        box.contentHeight = Math.max(0, content[5] - content[1]);
        evidence.gutters = { top, right, bottom, left };
        if (both && (horizontalInline ? evidence.allottedWidth < 2 * metrics.vertical : evidence.allottedHeight < 2 * metrics.horizontal)) {
          observation.unsupported.push({ node: evidence.node, feature: 'tiny-mirrored-scrollbar-geometry',
            reason: 'Chrome tiny mirrored content quads and client offsets can disagree with actual child origins. They cannot represent the synthetic equally clamped pair reliably.', evidence });
        }
        const verticalSize = Math.min(metrics.vertical, evidence.allottedWidth / (both && horizontalInline ? 2 : 1));
        const horizontalSize = Math.min(metrics.horizontal, evidence.allottedHeight / (both && !horizontalInline ? 2 : 1));
        const fits = (start, end, size, atStart, mirrored) => {
          if (start === 0 && end === 0) return true;
          return mirrored ? start === size && end === size : start === (atStart ? size : 0) && end === (atStart ? 0 : size);
        };
        if (!fits(left, right, verticalSize, metrics.verticalOnLeft, both && horizontalInline)
          || !fits(top, bottom, horizontalSize, metrics.horizontalOnTop, both && !horizontalInline)) {
          observation.unsupported.push({ node: evidence.node, feature: 'native-scrollbar-host-policy',
            reason: 'Observed Chrome gutter thickness, side, or tiny mirrored geometry does not represent the synthetic host clamping/side policy.', evidence });
        }
      } else if (width === 'thin'
        && (Math.abs(evidence.width - evidence.border.left - evidence.border.right - evidence.clientWidth) > 0.5
          || Math.abs(evidence.height - evidence.border.top - evidence.border.bottom - evidence.clientHeight) > 0.5)) {
        observation.unsupported.push({ node: evidence.node, feature: 'native-thin-scrollbar-policy',
          reason: 'Native thin scrollbars reserve space on this Chrome platform; the requested zero-overlay policy is not representable.', evidence });
      }
      Object.assign(box, { scrollbarGutterTop: top, scrollbarGutterRight: right, scrollbarGutterBottom: bottom, scrollbarGutterLeft: left });
      const range = evidence.scrollRange;
      const allowsScrollbar = width !== 'none' && !evidence.replaced;
      box.horizontalScrollbar = allowsScrollbar && (overflowX === 'scroll' || overflowX === 'auto'
        && (range.maxX !== range.minX || (top + bottom > 0 && (!stable || horizontalInline))));
      box.verticalScrollbar = allowsScrollbar && (overflowY === 'scroll' || overflowY === 'auto'
        && (range.maxY !== range.minY || (left + right > 0 && (!stable || !horizontalInline))));
      const horizontalUsed = box.horizontalScrollbar || allowsScrollbar && stable && !horizontalInline && scrollable(overflowX);
      const verticalUsed = box.verticalScrollbar || allowsScrollbar && stable && horizontalInline && scrollable(overflowY);
      if (metrics && (horizontalUsed || verticalUsed) && width === 'thin'
        && (!metrics.overlay || horizontalUsed && metrics.horizontal !== 0 || verticalUsed && metrics.vertical !== 0)) {
        observation.unsupported.push({ node: evidence.node, feature: 'thin-scrollbar-host-policy',
          reason: 'Chrome scrollbar-width:thin overrides ::-webkit-scrollbar dimensions. The exercised custom classic/nonzero thin host policy cannot be injected faithfully.', evidence });
      } else if (metrics?.overlay && (horizontalUsed && metrics.horizontal !== 0 || verticalUsed && metrics.vertical !== 0)) {
        observation.unsupported.push({ node: evidence.node, feature: 'overlay-scrollbar-host-policy',
          reason: 'The adapter cannot inject or measure the exercised nonzero native overlay scrollbar thickness.', evidence });
      }
    }
  } finally {
    if (retainedObjects) await browser.send('Runtime.releaseObjectGroup', { objectGroup: group }, browser.sessionId);
  }
}

async function runChrome(input) {
  const fontAssets = await fixtureFonts(input);
  if (!browser) await startChrome();
  await browser.send('Emulation.setDeviceMetricsOverride', { width: input.viewport.width, height: input.viewport.height, deviceScaleFactor: 1, mobile: false }, browser.sessionId);
  const columns = columnGeometry(input);
  if (columns?.unsupported) throw new Error(columns.unsupported);
  const result = await browser.send('Runtime.evaluate', { expression: `(${observeDOM.toString()})(${JSON.stringify(input)},${JSON.stringify(fontAssets)},${JSON.stringify(columns)})`, returnByValue: true, awaitPromise: true }, browser.sessionId);
  if (result.exceptionDetails) throw new Error(`DOM adapter failed: ${result.exceptionDetails.exception?.description ?? result.exceptionDetails.text}`);
  if (!result.result.value?.sources) throw new Error('Chrome did not return source geometry');
  if (result.result.value.documentMode !== 'CSS1Compat') throw new Error('Chrome oracle requires standards mode');
  summary.browser.documentMode = result.result.value.documentMode;
  await resolveScrollbarGeometry(result.result.value);
  return result.result.value;
}

function compare(actual, expected, compareRects = false) {
  if (!expected || !Array.isArray(expected.sources) || expected.sources.length !== actual.sources.length) throw new Error('Expected sources must match the protocol node count');
  const mismatches = [];
  expected.sources.forEach((box, node) => {
    if (!box || !Object.keys(box).length) throw new Error(`Expected source ${node} has no assertions`);
    for (const [field, value] of Object.entries(box)) {
      if (field === 'rects') {
        if (!compareRects) continue;
        if (!Array.isArray(value)) throw new Error(`Expected source ${node}.rects must be an array`);
        const observed = actual.sources[node].rects;
        if (observed.length !== value.length) mismatches.push({ node, field: 'rects.length', expected: value.length, actual: observed.length, delta: observed.length - value.length });
        value.forEach((rect, index) => {
          if (!rect || Object.keys(rect).length !== 4 || rectangleFields.some(key => !Number.isFinite(rect[key]) || (key === 'width' || key === 'height') && rect[key] < 0)) throw new Error(`Invalid expected source ${node}.rects[${index}]`);
          for (const key of rectangleFields) {
            const measured = observed[index]?.[key];
            if (!Number.isFinite(measured) || Math.abs(measured - rect[key]) > options.tolerance) mismatches.push({ node, field: `rects[${index}].${key}`, expected: rect[key], actual: measured, delta: measured - rect[key] });
          }
        });
        continue;
      }
      if (field === 'kind' || field === 'text') {
        if (field === 'kind' ? !['element', 'text'].includes(value) : typeof value !== 'string') throw new Error(`Invalid expected source ${node}.${field}`);
        if (actual.sources[node][field] !== value) mismatches.push({ node, field, expected: value, actual: actual.sources[node][field] });
        continue;
      }
      if (field === 'firstBox') {
        if (value !== null && (!Number.isInteger(value) || value < 0)) throw new Error(`Invalid expected source ${node}.firstBox`);
        if (actual.sources[node].firstBox !== value) mismatches.push({ node, field, expected: value, actual: actual.sources[node].firstBox });
        continue;
      }
      if (!sourceFields.includes(field)) throw new Error(`Unknown expected field ${field}`);
      if (booleanFields.includes(field) ? typeof value !== 'boolean' : !Number.isFinite(value)) throw new Error(`Invalid expected source ${node}.${field}`);
      if (gutterFields.includes(field) && value < 0) throw new Error(`Invalid expected source ${node}.${field}`);
      const observed = actual.sources[node][field];
      if (typeof value === 'boolean' ? observed !== value : !Number.isFinite(observed) || Math.abs(observed - value) > options.tolerance) mismatches.push({ node, field, expected: value, actual: observed, ...(typeof value === 'number' ? { delta: observed - value } : {}) });
    }
  });
  if (Object.hasOwn(expected, 'viewportOverflow')) {
    const rectangle = expected.viewportOverflow;
    if (!rectangle || typeof rectangle !== 'object' || Array.isArray(rectangle) || Object.keys(rectangle).length !== rectangleFields.length || rectangleFields.some(field => !Object.hasOwn(rectangle, field))) throw new Error('Expected viewportOverflow must be an x/y/width/height rectangle');
    for (const field of rectangleFields) {
      const value = rectangle[field];
      if (!Number.isFinite(value) || (['width', 'height'].includes(field) && value < 0)) throw new Error(`Invalid expected viewportOverflow.${field}`);
      const observed = actual.viewportOverflow?.[field];
      if (!Number.isFinite(observed) || Math.abs(observed - value) > options.tolerance) mismatches.push({ node: null, field: `viewportOverflow.${field}`, expected: value, actual: observed, delta: observed - value });
    }
  }
  return mismatches;
}

async function saveFailure(name, input, detail, suffix = '') {
  const directory = join(root, '.cache/chrome-diff');
  await mkdir(directory, { recursive: true });
  const stem = name.replace(/[^a-zA-Z0-9_.-]/g, '_');
  const path = join(directory, `${stem}${suffix}.json`);
  await writeFile(path, `${JSON.stringify(input, null, 2)}\n`);
  if (detail) await writeFile(join(directory, `${stem}${suffix}.result.json`), `${JSON.stringify(detail, null, 2)}\n`);
  return path;
}

async function runFixture(fixture, source, isFuzz = false) {
  const result = { name: fixture.name ?? source, source, kind: fixture.kind, features: fixture.features ?? [] };
  summary.totals.discovered++;
  summary.totals[isFuzz ? 'fuzz' : fixture.kind]++;
  try {
    validateInput(fixture.input);
    const actual = runNative(fixture.input);
    if (fixture.kind === 'spec') {
      if (!Array.isArray(fixture.spec) || !fixture.spec.length) throw new Error('Spec fixtures require normative source URLs');
      result.spec = fixture.spec;
      result.mismatches = compare(actual, fixture.expected, true);
    } else {
      let unsupported = unsupportedAdapter(fixture.input, actual);
      if (unsupported.some(reason => reason.feature === 'fragmentation-geometry')
        || unsupported.length && !fixture.input.nodes.some(node => Object.hasOwn(node, 'text'))) {
        result.status = 'unsupported'; result.unsupported = unsupported;
        summary.totals.excludedUnsupported++; summary.results.push(result);
        return result;
      }
      const expected = await runChrome(fixture.input);
      unsupported = unsupportedAdapter(fixture.input, actual, expected.fontEvidence);
      if (expected.fontEvidence?.length) result.fontEvidence = expected.fontEvidence;
      if (expected.textEvidence?.length) result.textEvidence = expected.textEvidence;
      if (expected.textJustifyEvidence?.length) result.textJustifyEvidence = expected.textJustifyEvidence;
      if (expected.textIndentEvidence?.length) result.textIndentEvidence = expected.textIndentEvidence;
      if (expected.marginBreakEvidence?.length) result.marginBreakEvidence = expected.marginBreakEvidence;
      if (expected.gridEvidence?.length) result.gridEvidence = expected.gridEvidence;
      if (fixture.input.fragmentation || fixture.input.nodes.some(node => Object.hasOwn(node, 'text'))) result.browserSources = expected.sources;
      result.scrollOffsets = expected.scrollOffsets;
      if (expected.resources?.length) result.resources = expected.resources;
      if (expected.scrollbarEvidence?.length) result.scrollbarEvidence = expected.scrollbarEvidence;
      if (unsupported.length || expected.unsupported?.length) {
        result.status = 'unsupported'; result.unsupported = [...unsupported, ...(expected.unsupported ?? [])];
        summary.totals.excludedUnsupported++; summary.results.push(result);
        return result;
      }
      result.mismatches = compare(actual, expected, Boolean(fixture.input.fragmentation) || result.features.includes('inline-fragments'));
    }
    summary.totals.mismatches += result.mismatches.length;
    result.status = result.mismatches.length ? 'failed' : 'passed';
    if (result.status === 'failed') result.reproduction = await saveFailure(`${fixture.kind}-${result.name}`, fixture.kind === 'spec' ? fixture : fixture.input, result);
    summary.totals[result.status]++;
  } catch (error) {
    result.status = 'failed'; result.error = error.message; summary.totals.failed++;
    if (fixture.input) result.reproduction = await saveFailure(`${fixture.kind}-${result.name}`, fixture.kind === 'spec' ? fixture : fixture.input, result);
  }
  summary.results.push(result);
  return result;
}

function randomSource(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = Math.imul(state ^ state >>> 15, state | 1);
    value ^= value + Math.imul(value ^ value >>> 7, value | 61);
    return ((value ^ value >>> 14) >>> 0) / 4294967296;
  };
}

function fuzzInput(random) {
  const integer = (low, high) => low + Math.floor(random() * (high - low + 1));
  const pick = values => values[integer(0, values.length - 1)];
  const input = { viewport: { width: 800, height: 600 }, nodes: [] };
  const count = integer(3, 12);
  for (let i = 0; i < count; i++) {
    const parent = i === 0 ? null : integer(0, i - 1);
    const style = { display: pick(['block', 'flex']), width: i === 0 ? integer(120, 500) : pick(['auto', integer(20, 180), { percent: pick([0.25, 0.5, 0.75]) }]),
      height: i === 0 ? integer(100, 400) : pick(['auto', integer(10, 100)]),
      padding: integer(0, 12), margin: { top: integer(-5, 15), right: integer(0, 10), bottom: integer(-5, 15), left: integer(0, 10) } };
    if (random() < 0.5) { style.minWidth = integer(0, 40); style.maxWidth = integer(60, 240); }
    if (random() < 0.3) { style.minHeight = integer(0, 20); style.maxHeight = integer(40, 160); }
    if (random() < 0.3) style.marginLeft = 'auto';
    if (random() < 0.25) style.boxSizing = 'border-box';
    if (style.display === 'flex') Object.assign(style, { flexDirection: pick(['row', 'row-reverse', 'column', 'column-reverse']), flexWrap: pick(['nowrap', 'wrap', 'wrap-reverse']),
      rowGap: integer(0, 12), columnGap: integer(0, 12), alignItems: pick(['stretch', 'flex-start', 'center', 'flex-end']),
      justifyContent: pick(['flex-start', 'center', 'space-between']), alignContent: pick(['stretch', 'flex-start', 'center']) });
    if (parent !== null && input.nodes[parent].style.display === 'flex') Object.assign(style, {
      flexGrow: pick([0, 0.2, 0.5, 1, 2]), flexShrink: pick([0, 0.25, 1, 2]), flexBasis: pick(['auto', 0, integer(20, 100), { percent: 0.25 }]), order: integer(-2, 2) });
    input.nodes.push({ parent, style });
  }
  return input;
}

function gridFuzzInput(random, index) {
  const integer = (low, high) => low + Math.floor(random() * (high - low + 1));
  const pick = values => values[integer(0, values.length - 1)];
  const rows = integer(2, 5), columns = integer(2, 5);
  const tracks = count => Array.from({ length: count }, () => pick([integer(24, 64), { fr: integer(1, 3) }]));
  const implicitTracks = () => Array.from({ length: integer(2, 3) }, () => integer(24, 48));
  const placement = (count, definite) => {
    const span = { span: integer(1, 3) };
    if (!definite) return pick([['auto', 'auto'], ['auto', span], [span, 'auto']]);
    const line = () => pick([-1, 1]) * integer(1, count + 3);
    const start = line(), end = line();
    return pick([[start, 'auto'], ['auto', end], [start, span], [span, end],
      [{ line: start }, { line: end }]]);
  };
  const input = { viewport: { width: 800, height: 600 }, nodes: [{
    parent: null, style: {
      display: 'grid', boxSizing: 'border-box', width: 720, height: 560,
      gridTemplateRows: tracks(rows), gridTemplateColumns: tracks(columns),
      gridAutoRows: implicitTracks(), gridAutoColumns: implicitTracks(),
      gridAutoFlow: ['row', 'column', 'row dense', 'column dense'][index % 4],
      rowGap: integer(0, 8), columnGap: integer(0, 8),
      justifyContent: 'start', alignContent: 'start', justifyItems: 'start', alignItems: 'start'
    }
  }] };
  // Rotate template families independently of flow, so each family exercises
  // all four flows. This consumes only the domain-separated grid RNG.
  const family = Math.floor(index / 4) % 6;
  const rootStyle = input.nodes[0].style;
  const namedTracks = (count, name) => [
    { names: [`${name}-start`, name] },
    ...tracks(count).flatMap(track => [track, { names: [name] }]),
    { names: [`${name}-end`] }
  ];
  const namedPlacement = name => pick([
    [name, 'auto'],
    [{ line: integer(1, 3), name }, { span: 1 }],
    [{ line: -integer(1, 3), name }, 'auto'],
    [1, { span: integer(1, 3), name }],
    [{ span: integer(1, 3), name }, -1],
    [{ line: integer(1, 2), name: `${name}-missing` }, 'auto']
  ]);
  if (family === 1) {
    rootStyle.gridTemplateRows = namedTracks(rows, 'row-edge');
    rootStyle.gridTemplateColumns = namedTracks(columns, 'column-edge');
  } else if (family === 2) {
    rootStyle.gridTemplateAreas = [
      ['header', 'header', 'header'],
      ['nav', 'main', 'main'],
      ['nav', '.', '.']
    ];
    // Sometimes areas, rather than authored sizes, establish explicit extent.
    rootStyle.gridTemplateRows = pick(['none', [32], [32, 48, 24]]);
    rootStyle.gridTemplateColumns = pick(['none', [48], [48, 64, 32]]);
  } else if (family >= 3) {
    const count = family === 3 ? integer(2, 4) : family === 4 ? 'auto-fill' : 'auto-fit';
    const fixed = () => pick([
      integer(32, 56), { percent: 0.15 },
      { minmax: [integer(24, 40), { fr: integer(1, 2) }] },
      { minmax: ['min-content', integer(40, 56)] }
    ]);
    const pattern = [
      { names: ['column-edge'] }, fixed(), { names: ['column-middle'] }
    ];
    if (integer(0, 1)) pattern.push(fixed(), { names: ['column-edge'] });
    rootStyle.width = integer(220, 320);
    rootStyle.gridTemplateColumns = [
      { names: ['shell-start'] }, 24,
      { names: ['repeat-start'] }, { repeat: { count, tracks: pattern } },
      { names: ['repeat-end'] }, 24, { names: ['shell-end'] }
    ];
    // A second, adjacent integer repetition remains legal beside an auto repeat.
    if (integer(0, 1)) rootStyle.gridTemplateColumns.splice(4, 0,
      { repeat: { count: 2, tracks: [{ names: ['column-edge'] }, 16] } });
  }
  const count = family >= 4 ? integer(1, 6) : integer(8, 20);
  for (let i = 0; i < count; i++) {
    // Mix automatic, row-locked, column-locked and definite items in source order.
    let [gridRowStart, gridRowEnd] = placement(rows, i % 6 === 3 || i % 6 === 5);
    let [gridColumnStart, gridColumnEnd] = placement(columns, i % 6 === 4 || i % 6 === 5);
    if (family === 1 && i % 2 === 0) {
      [gridRowStart, gridRowEnd] = namedPlacement('row-edge');
      [gridColumnStart, gridColumnEnd] = namedPlacement('column-edge');
    } else if (family === 2 && i % 2 === 0) {
      const area = pick(['header', 'nav', 'main']);
      gridRowStart = gridRowEnd = gridColumnStart = gridColumnEnd = area;
      if (i % 4 === 0) {
        gridRowStart = `${area}-start`;
        gridColumnEnd = `${area}-end`;
      }
    } else if (family >= 3 && i % 3 === 0) {
      [gridColumnStart, gridColumnEnd] = namedPlacement('column-edge');
    }
    input.nodes.push({ parent: 0, style: {
      display: 'block', boxSizing: 'border-box', width: integer(6, 18), height: integer(6, 18),
      order: [-2, 1, -2, 0, 1][i % 5],
      gridRowStart, gridRowEnd, gridColumnStart, gridColumnEnd
    } });
  }
  // Alignment has its own stream, preserving every earlier placement/template
  // draw and the legacy block/flex stream. Keep one fifth as unchanged controls.
  if (index % 5 !== 0) {
    const alignmentRandom = randomSource((options.seed ^ 0x616c6967 ^ index) >>> 0);
    const alignmentPick = values => values[Math.floor(alignmentRandom() * values.length)];
    const modes = ['horizontal-tb', 'vertical-rl', 'vertical-lr', 'sideways-rl', 'sideways-lr'];
    const positions = ['start', 'end', 'center', 'self-start', 'self-end'];
    const self = [...positions, 'stretch', 'baseline', 'last baseline'];
    const distribution = ['start', 'end', 'center', 'stretch', 'space-between', 'space-around', 'space-evenly'];
    const safety = (style, property) => {
      if (positions.includes(style[property]) || ['left', 'right'].includes(style[property])) {
        style[`${property}Safety`] = alignmentPick(['default', 'safe', 'unsafe']);
      }
    };
    rootStyle.writingMode = modes[Math.floor(index / 4) % modes.length];
    rootStyle.direction = alignmentPick(['ltr', 'rtl']);
    rootStyle.justifyContent = alignmentPick(distribution);
    rootStyle.alignContent = alignmentPick(distribution);
    rootStyle.justifyItems = alignmentPick([...self, 'left', 'right']);
    rootStyle.alignItems = alignmentPick(self);
    for (const property of ['justifyContent', 'alignContent', 'justifyItems', 'alignItems']) safety(rootStyle, property);
    if (index % 4 === 0) rootStyle.height = 80;
    if (index % 4 === 1) rootStyle.width = 80;
    if (index % 7 === 1) { rootStyle.display = 'inline-grid'; delete rootStyle.width; }
    if (index % 7 === 2) delete rootStyle.height;
    if (index % 8 === 1) {
      rootStyle.overflowX = rootStyle.overflowY = 'auto';
      input.nodes[0].scrollOffset = {
        x: alignmentPick([-80, 0, 80]), y: alignmentPick([-80, 0, 80])
      };
    }
    for (const node of input.nodes.slice(1)) {
      const style = node.style;
      style.writingMode = alignmentPick([rootStyle.writingMode, rootStyle.writingMode, ...modes]);
      style.direction = alignmentPick([rootStyle.direction, 'ltr', 'rtl']);
      if (alignmentRandom() < 0.5) style.justifySelf = alignmentPick(['auto', ...self, 'left', 'right']);
      if (alignmentRandom() < 0.5) style.alignSelf = alignmentPick(['auto', ...self]);
      safety(style, 'justifySelf'); safety(style, 'alignSelf');
      if (alignmentRandom() < 0.3) {
        style[alignmentPick(['marginLeft', 'marginRight', 'marginTop', 'marginBottom'])] = 'auto';
      }
      if (alignmentRandom() < 0.2) {
        const dimension = alignmentPick(['width', 'height']);
        if (alignmentRandom() < 0.5) delete style[dimension];
        else style[dimension] = { percent: 0.5 };
      }
    }
  }
  return input;
}

// Bounded structural reduction preserves the discrepancy field and delta sign.
// Original input is always retained; reduction stops after a bounded leaf sweep,
// without claiming globally minimal CSS or a normative correctness oracle.
async function reduceFailure(input, mismatch) {
  let reduced = structuredClone(input), attempts = 0, changed = true;
  while (changed && attempts < 40) {
    changed = false;
    for (let i = reduced.nodes.length - 1; i > 0 && attempts < 40; i--) {
      if (reduced.nodes.some(node => node.parent === i)) continue;
      const candidate = structuredClone(reduced);
      candidate.nodes.splice(i, 1);
      for (const node of candidate.nodes) if (node.parent > i) node.parent--;
      attempts++;
      try {
        const expected = await runChrome(candidate);
        if (expected.unsupported?.length) continue;
        const diffs = compare(runNative(candidate), expected);
        if (diffs.some(diff => diff.field === mismatch.field && Math.sign(diff.delta) === Math.sign(mismatch.delta))) {
          reduced = candidate; changed = true; break;
        }
      } catch { /* An unrelated crash does not preserve the geometric discrepancy. */ }
    }
  }
  return { input: reduced, attempts, reduction: 'Leaf deletion preserving a field and delta-sign discrepancy; at most 40 attempts.' };
}

async function main() {
  if (!parseArgs()) return;
  await access(options.cli, constants.X_OK);
  const files = await fixtureFiles(options.path);
  if (!files.length && !options.fuzz && !options.gridFuzz) throw new Error('No fixture files found');
  for (const source of files) {
    let fixture;
    try {
      fixture = JSON.parse(await readFile(source, 'utf8'));
      // Saved protocol reproductions are directly runnable as differential cases.
      if (fixture.nodes) fixture = { name: source, kind: 'chrome', input: fixture };
      if (!['spec', 'chrome'].includes(fixture.kind)) throw new Error('Fixture kind must be spec or chrome');
      if (options.mode !== 'all' && fixture.kind !== options.mode) continue;
    } catch (error) {
      summary.totals.discovered++; summary.totals.failed++;
      summary.results.push({ source, status: 'failed', error: error.message }); continue;
    }
    await runFixture(fixture, source);
  }
  const random = randomSource(options.seed);
  for (let index = 0; index < options.fuzz; index++) {
    const input = fuzzInput(random), name = `fuzz-${options.seed}-${index}`;
    const result = await runFixture({ name, kind: 'chrome', input, features: ['seeded-rectangle-tree'] }, name, true);
    if (result.mismatches?.length) {
      const reduced = await reduceFailure(input, result.mismatches[0]);
      result.reducedReproduction = await saveFailure(name, reduced.input, { attempts: reduced.attempts, reduction: reduced.reduction }, '.reduced');
    }
  }
  // Domain-separate the grid stream; neither its count nor its draws affect --fuzz.
  const gridRandom = randomSource((options.seed ^ 0x67726964) >>> 0);
  for (let index = 0; index < options.gridFuzz; index++) {
    const input = gridFuzzInput(gridRandom, index), name = `grid-fuzz-${options.seed}-${index}`;
    const result = await runFixture({ name, kind: 'chrome', input, features: ['seeded-grid-auto-placement', 'seeded-grid-names-areas-repeat', 'seeded-grid-alignment-baselines-scroll'] }, name, true);
    if (result.mismatches?.length) {
      const reduced = await reduceFailure(input, result.mismatches[0]);
      result.reducedReproduction = await saveFailure(name, reduced.input, {
        seed: options.seed, index, attempts: reduced.attempts, reduction: reduced.reduction
      }, '.reduced');
    }
  }
  if (!summary.totals.discovered) throw new Error('No fixtures selected');
  summary.ok = summary.totals.failed === 0 && summary.totals.excludedUnsupported === 0;
  process.exitCode = summary.ok ? 0 : 1;
}

try {
  await main();
} catch (error) {
  summary.errors.push(error.message); summary.ok = false; process.exitCode = 1;
} finally {
  if (browser) {
    try { await browser.close(); } catch (error) { summary.errors.push(`Chrome cleanup: ${error.message}`); summary.ok = false; process.exitCode = 1; }
  }
  if (!process.argv.includes('--help') && !process.argv.includes('-h')) process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
}
