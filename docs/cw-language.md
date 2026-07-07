# CW Language Contract

This document records the `.cw` surface accepted by the repository's real
host-side front-end, `waugen.cw_lang`, and how that relates to the narrower
`compile-cw` RTL-lowering template.

`cw_lang.py` is the lexer, parser, AST, and tree-walking interpreter for
compile-time behavior. It is intentionally independent of `cw_compiler.py`,
which extracts a fixed kernel shape and lowers it into WAU flow/program config.

## Validation Commands

Validate general `.cw` syntax and `// @wau` pragmas without lowering to RTL:

```bash
PYTHONPATH=src/python python3 -m waugen cw-lint \
  --program-file CWs/samples/types/fixed_point.cw
```

Also require compatibility with the current `compile-cw` kernel template:

```bash
PYTHONPATH=src/python python3 -m waugen cw-lint \
  --program-file CWs/example-program.cw \
  --compile-template
```

Machine-readable output is available with `--json`.

## Lexical Rules

- Whitespace separates tokens and is otherwise insignificant.
- Line comments begin with `//`.
- Block comments use `/* ... */`.
- Identifiers match C-like names: a letter or `_`, then letters, digits, or `_`.
- Integer literals are decimal.
- Float literals accept decimal points, scientific notation, and optional `f`
  suffixes, such as `1.0`, `.5`, `1e-3`, and `3.0f`.
- String literals use double quotes and support common escapes such as `\n`,
  `\t`, `\"`, and `\\`.
- Boolean literals are `true` and `false`.

## Top-Level Grammar

The parser accepts these top-level declarations:

```ebnf
program       := top_decl* EOF ;
top_decl      := alias_decl | class_decl | function_decl ;
alias_decl    := "alias" IDENT "=" type_name ";" ;
class_decl    := ("class" | "space") IDENT "{" class_member* "}" ;
function_decl := type_name IDENT "(" params? ")" block ;
type_name     := "void" | IDENT ;
```

`space` is accepted as a legacy class-like declaration because existing sample
programs use it for kernel objects.

## Class Members

Classes and spaces may contain fields, nested classes/spaces, and methods:

```ebnf
class_member := class_decl | field_decl | method_decl ;
field_decl   := ("DRAM")? type_name IDENT dims? ("=" expr)? ";" ;
method_decl  := (type_name)? IDENT "(" params? ")" block ;
dims         := "[" expr ("," expr)* "]" ;
params       := param ("," param)* ;
param        := IDENT | ("DRAM")? type_name IDENT ("[]")* ;
```

Untyped parameters are accepted for magic methods, for example
`__add__(other)`.

Supported magic methods include:

- `__init__`
- arithmetic: `__add__`, `__sub__`, `__mul__`, `__div__`, `__mod__`
- comparisons: `__eq__`, `__ne__`, `__lt__`, `__gt__`, `__le__`, `__ge__`
- unary: `__neg__`
- conversions: `__to_int__`, `__to_float__`, `__convert__`
- string form: `__str__`

## Statements

Function and method bodies accept:

```ebnf
block       := "{" statement* "}" ;
statement   := block
             | var_decl
             | assignment ";"
             | expr ";"
             | "return" expr? ";"
             | "if" "(" expr ")" statement ("else" statement)?
             | "for" "(" for_init? ";" expr? ";" for_update? ")" statement ;
var_decl    := ("DRAM")? type_name IDENT dims? ("=" expr)? ";" ;
assignment  := expr ("=" | "+=" | "-=" | "*=" | "/=") expr ;
for_init    := var_decl | assignment | expr ;
for_update  := assignment | expr ;
```

Postfix `++` and `--` are parsed as statement updates.

## Expressions

Expressions are C-like and include:

- integer, float, string, and boolean literals
- names and member access, such as `obj.field`
- calls, such as `f(a, b)` or `obj.method(a)`
- construction, such as `new q8_8(384)`
- multi-dimensional indexing, such as `a[i, j]`
- unary `-` and `!`
- arithmetic `*`, `/`, `%`, `+`, `-`
- comparisons `<`, `>`, `<=`, `>=`, `==`, `!=`
- logical `&&`, `||`
- builtin dtype casts, such as `int32(x)` and `float32(x)`
- C-style casts for builtin dtypes or aliases, such as `(float32)x`

When the left operand is a class instance, arithmetic and comparison operators
dispatch to the matching magic method on that instance. Builtin dtype casts on a
class instance dispatch to conversion hooks. The same conversion path is exposed
programmatically as `Interpreter.convert(value, dtype)`.

## WAU Pragmas

`compile-cw` and `cw-lint` validate these line pragmas:

```c
// @wau lane_parallelism=4
// @wau max_in_flight=4
// @wau preferred_dtype=float32
// @wau placement_policy=locality
// @wau lowering_profile=latency_optimized
// @wau program_priority=4
// @wau program_load_balance=least_busy
```

Pragma values must be single tokens. Duplicate keys are rejected. Invalid
syntax, unknown keys, and invalid values produce deterministic errors with
1-based line numbers.

Supported values:

- `lane_parallelism`: integer >= 1
- `max_in_flight`: integer >= 1
- `preferred_dtype`: lowercase dtype token
- `placement_policy`: `locality` or `balance`
- `lowering_profile`: `reference`, `latency_optimized`, or
  `throughput_optimized`
- `program_priority`: integer >= 1
- `program_load_balance`: `least_busy` or `round_robin`

## RTL-Lowering Template

General `.cw` syntax validation does not imply the program can be lowered to
RTL. The current `compile-cw` path still requires the kernel-template surface
used by `CWs/example-program.cw`:

- a `space <kernel_name> { ... }` block,
- integer constants `K`, `TILE_H`, `TILE_W`, `CIN_BLOCK`, and `COUT_BLOCK`,
- a `channel_worker workers[N];` declaration,
- calls or definitions containing these symbols:
  - `load_input_block(`
  - `prefetch_input_block(`
  - `load_weight_block(`
  - `accumulate_current_block(`
  - `finalize_bias_residual_relu(`
  - `store_output_tile(`

Use `cw-lint --compile-template` when a source file is intended for
`compile-cw`.
