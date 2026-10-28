# Silk limitations

## 1. Template formatting inside a borrowed match

- Status: [silk-lang/silk#2390](https://github.com/silk-lang/silk/issues/2390), filed against published `0.1.0-dev.20+10a8c67813a1` on macOS arm64.
- Failure: `StderrWriter.print` inside a borrowed `Option<&Value>` match produces SEM0042 and an unsupported-construct diagnostic in `validatedTemplate`. Removing only the print statement makes the minimized package check successfully.
- Workaround: `cli/src/protocol.silk`, marked `LIMITATION-1`, writes field-error diagnostics through the standard `Writer` service. The compiler and stdlib remain unmodified.
- Cost: two field-diagnostic callers share the helper. Error-only path; no runtime performance penalty measured.
- Reproduction: self-contained package and exact diagnostics in the issue. Recheck on a toolchain upgrade before removing the workaround.
