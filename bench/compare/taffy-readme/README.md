Exports the trees behind the benchmark table in [taffy's README](https://github.com/DioxusLabs/taffy#benchmarks-vs-yoga),
built by taffy's own benchmark generators (`benches/benches/flexbox.rs`) with their fixed seed, as silk-layout CLI
documents. Taffy's benchmarks lay out under max-content available space; silk-layout always has a definite initial
containing block, so the documents use a 1000x800 viewport for all three engines.

    cargo run --release -- /tmp/taffy-readme
    python3 ../compare.py --silk ... --taffy ... --yoga ... --docs /tmp/taffy-readme --iterations 5
