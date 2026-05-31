# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

"""A real (non-regex) front-end for the ``.cw`` language: lexer -> AST ->
recursive-descent parser -> tree-walking interpreter.

Scope and intent
----------------
The existing :mod:`waugen.cw_compiler` is a *shape extractor*: it scrapes a few
dimensions/pragmas out of a ``.cw`` file with regexes and synthesises a fixed
WAU flow template. That path stays the source of truth for RTL lowering and the
benchmark.

This module is complementary. It actually *parses and executes* ``.cw`` code on
the host at compile time. Its headline feature is **classes with magic
methods** (Python-style dunders): user code can describe behaviour that "should
not run on the WAU" — most importantly **custom numeric types and their
conversions/promotions** — and the compiler can invoke those magic methods to
handle complex type-format conversion dynamically.

Supported surface (a pragmatic C-like subset)
---------------------------------------------
* top level: ``alias name = type;``, ``class``/``space`` declarations,
  free functions, and ``void main() { ... }``;
* class members: fields (incl. multi-dim arrays and ``DRAM`` handles), methods,
  and magic methods (``__init__``, ``__add__``/``__sub__``/``__mul__``/
  ``__div__``/``__mod__``, ``__eq__``/``__ne__``/``__lt__``/``__gt__``/
  ``__le__``/``__ge__``, ``__neg__``, conversion hooks ``__to_int__`` /
  ``__to_float__`` / ``__convert__``, and ``__str__``);
* statements: typed declarations, assignment (``= += -= *= /=``), ``++``/``--``,
  ``return``, ``if``/``else``, ``for``, blocks, expression statements;
* expressions: int/float/string/bool literals, identifiers, member access,
  calls, ``new C(...)``, multi-dim indexing ``a[i, j]``, the usual arithmetic /
  comparison / logical operators, and builtin dtype casts (``int32(x)`` etc).

Operator dispatch follows Python: ``a + b`` where ``a`` is a class instance
calls ``a.__add__(b)``. Builtin casts on an instance call its conversion magic
method (``float32(x)`` -> ``x.__to_float__()``), and :meth:`Interpreter.convert`
exposes the same dispatch to the rest of the toolchain.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


class CWLangError(ValueError):
    """Raised for lex/parse/runtime errors, with a 1-based line number."""


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

_KEYWORDS = {
    "alias",
    "class",
    "space",
    "new",
    "return",
    "if",
    "else",
    "for",
    "void",
    "DRAM",
    "true",
    "false",
}

# Numeric/dtype builtins that double as cast functions.
DTYPES = {
    "int8",
    "int16",
    "int32",
    "int64",
    "uint8",
    "uint16",
    "uint32",
    "float16",
    "bfloat16",
    "float32",
    "float64",
    "int",
    "float",
}

# Multi-character operators, checked longest-first.
_OPS = [
    "==",
    "!=",
    "<=",
    ">=",
    "&&",
    "||",
    "+=",
    "-=",
    "*=",
    "/=",
    "++",
    "--",
    "(",
    ")",
    "{",
    "}",
    "[",
    "]",
    ";",
    ",",
    ".",
    "=",
    "+",
    "-",
    "*",
    "/",
    "%",
    "<",
    ">",
    "!",
]


@dataclass(frozen=True)
class Token:
    kind: str  # INT | FLOAT | STRING | IDENT | KW | OP | EOF
    value: Any
    line: int


def tokenize(source: str) -> list[Token]:
    toks: list[Token] = []
    i = 0
    n = len(source)
    line = 1

    def err(msg: str) -> CWLangError:
        return CWLangError(f"line {line}: {msg}")

    while i < n:
        c = source[i]

        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r":
            i += 1
            continue

        # comments
        if c == "/" and i + 1 < n and source[i + 1] == "/":
            while i < n and source[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and source[i + 1] == "*":
            i += 2
            while i + 1 < n and not (source[i] == "*" and source[i + 1] == "/"):
                if source[i] == "\n":
                    line += 1
                i += 1
            i += 2
            continue

        # string literal
        if c == '"':
            j = i + 1
            buf = []
            escapes = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "\\": "\\"}
            while j < n and source[j] != '"':
                if source[j] == "\\" and j + 1 < n:
                    buf.append(escapes.get(source[j + 1], source[j + 1]))
                    j += 2
                    continue
                if source[j] == "\n":
                    line += 1
                buf.append(source[j])
                j += 1
            if j >= n:
                raise err("unterminated string literal")
            toks.append(Token("STRING", "".join(buf), line))
            i = j + 1
            continue

        # number
        if c.isdigit() or (c == "." and i + 1 < n and source[i + 1].isdigit()):
            j = i
            is_float = False
            while j < n and source[j].isdigit():
                j += 1
            if j < n and source[j] == ".":
                is_float = True
                j += 1
                while j < n and source[j].isdigit():
                    j += 1
            # optional exponent: e[+-]?digits
            if j < n and source[j] in "eE":
                k = j + 1
                if k < n and source[k] in "+-":
                    k += 1
                if k < n and source[k].isdigit():
                    is_float = True
                    j = k
                    while j < n and source[j].isdigit():
                        j += 1
            # optional float suffix 'f'
            if j < n and source[j] in "fF":
                is_float = True
                text = source[i:j]
                j += 1
            else:
                text = source[i:j]
            if is_float:
                toks.append(Token("FLOAT", float(text), line))
            else:
                toks.append(Token("INT", int(text), line))
            i = j
            continue

        # identifier / keyword
        if c.isalpha() or c == "_":
            j = i
            while j < n and (source[j].isalnum() or source[j] == "_"):
                j += 1
            word = source[i:j]
            kind = "KW" if word in _KEYWORDS else "IDENT"
            toks.append(Token(kind, word, line))
            i = j
            continue

        # operators / punctuation
        matched = None
        for op in _OPS:
            if source.startswith(op, i):
                matched = op
                break
        if matched is None:
            raise err(f"unexpected character {c!r}")
        toks.append(Token("OP", matched, line))
        i += len(matched)

    toks.append(Token("EOF", None, line))
    return toks


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------


@dataclass
class Param:
    name: str
    dtype: str
    is_dram: bool = False
    is_array: bool = False


@dataclass
class FieldDecl:
    name: str
    dtype: str
    dims: list["Node"]
    init: "Node | None"
    is_dram: bool
    line: int


@dataclass
class MethodDecl:
    name: str
    params: list[Param]
    body: list["Node"]
    return_type: str | None
    line: int


@dataclass
class ClassDecl:
    name: str
    fields: list[FieldDecl]
    methods: dict[str, MethodDecl]
    nested: dict[str, "ClassDecl"]
    line: int


@dataclass
class AliasDecl:
    name: str
    target: str
    line: int


@dataclass
class FuncDecl:
    name: str
    params: list[Param]
    body: list["Node"]
    return_type: str | None
    line: int


@dataclass
class Program:
    aliases: dict[str, AliasDecl]
    classes: dict[str, ClassDecl]
    functions: dict[str, FuncDecl]


# --- statements / expressions (Node) ---
@dataclass
class Node:
    line: int


@dataclass
class VarDecl(Node):
    name: str
    dtype: str
    dims: list[Node]
    init: Node | None
    is_dram: bool


@dataclass
class Assign(Node):
    target: Node
    op: str  # '=' '+=' '-=' '*=' '/='
    value: Node


@dataclass
class Return(Node):
    value: Node | None


@dataclass
class If(Node):
    cond: Node
    then: list[Node]
    otherwise: list[Node]


@dataclass
class For(Node):
    init: Node | None
    cond: Node | None
    update: Node | None
    body: list[Node]


@dataclass
class ExprStmt(Node):
    expr: Node


@dataclass
class Literal(Node):
    value: Any


@dataclass
class Name(Node):
    name: str


@dataclass
class Member(Node):
    obj: Node
    name: str


@dataclass
class Index(Node):
    obj: Node
    args: list[Node]


@dataclass
class Call(Node):
    callee: Node
    args: list[Node]


@dataclass
class New(Node):
    class_name: str
    args: list[Node]


@dataclass
class Unary(Node):
    op: str
    operand: Node


@dataclass
class Binary(Node):
    op: str
    left: Node
    right: Node


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_ASSIGN_OPS = {"=", "+=", "-=", "*=", "/="}


class Parser:
    def __init__(self, toks: list[Token]):
        self.toks = toks
        self.pos = 0
        self._alias_names: set[str] = set()

    # -- token helpers --
    def _peek(self, ahead: int = 0) -> Token:
        idx = min(self.pos + ahead, len(self.toks) - 1)
        return self.toks[idx]

    def _next(self) -> Token:
        tok = self.toks[self.pos]
        if tok.kind != "EOF":
            self.pos += 1
        return tok

    def _at(self, value: str) -> bool:
        tok = self._peek()
        return tok.kind in ("OP", "KW") and tok.value == value

    def _eat(self, value: str) -> Token:
        tok = self._peek()
        if not (tok.kind in ("OP", "KW") and tok.value == value):
            raise CWLangError(f"line {tok.line}: expected {value!r}, got {tok.value!r}")
        return self._next()

    def _eat_ident(self) -> str:
        tok = self._peek()
        if tok.kind != "IDENT":
            raise CWLangError(f"line {tok.line}: expected identifier, got {tok.value!r}")
        self._next()
        return tok.value

    # -- top level --
    def parse_program(self) -> Program:
        aliases: dict[str, AliasDecl] = {}
        classes: dict[str, ClassDecl] = {}
        functions: dict[str, FuncDecl] = {}

        while self._peek().kind != "EOF":
            if self._at("alias"):
                a = self._parse_alias()
                aliases[a.name] = a
            elif self._at("class") or self._at("space"):
                c = self._parse_class()
                classes[c.name] = c
            else:
                f = self._parse_function()
                functions[f.name] = f

        return Program(aliases=aliases, classes=classes, functions=functions)

    def _parse_alias(self) -> AliasDecl:
        line = self._eat("alias").line
        name = self._eat_ident()
        self._eat("=")
        target = self._eat_ident() if self._peek().kind == "IDENT" else self._next().value
        self._eat(";")
        self._alias_names.add(name)
        return AliasDecl(name=name, target=str(target), line=line)

    def _parse_class(self) -> ClassDecl:
        tok = self._next()  # 'class' or 'space'
        name = self._eat_ident()
        self._eat("{")
        fields: list[FieldDecl] = []
        methods: dict[str, MethodDecl] = {}
        nested: dict[str, ClassDecl] = {}

        while not self._at("}"):
            if self._at("class") or self._at("space"):
                c = self._parse_class()
                nested[c.name] = c
                continue
            member = self._parse_member()
            if isinstance(member, MethodDecl):
                methods[member.name] = member
            else:
                fields.append(member)

        self._eat("}")
        return ClassDecl(name=name, fields=fields, methods=methods, nested=nested, line=tok.line)

    def _is_type_token(self, tok: Token) -> bool:
        # A type position accepts 'void', a dtype/alias/class identifier.
        if tok.kind == "KW" and tok.value == "void":
            return True
        return tok.kind == "IDENT"

    def _parse_member(self):
        """Parse a class member: a field or a (possibly magic) method."""
        is_dram = False
        if self._at("DRAM"):
            self._next()
            is_dram = True

        # Magic/implicit-return-type method: IDENT '(' ...
        if (
            not is_dram
            and self._peek().kind == "IDENT"
            and self._peek(1).kind == "OP"
            and self._peek(1).value == "("
        ):
            return self._parse_method(return_type=None)

        return_type = self._parse_type_name()
        name = self._eat_ident()

        if self._at("("):
            if is_dram:
                raise CWLangError(f"line {self._peek().line}: DRAM cannot prefix a method")
            return self._parse_method(return_type=return_type, name=name)

        return self._finish_field(name=name, dtype=return_type, is_dram=is_dram)

    def _parse_type_name(self) -> str:
        tok = self._peek()
        if not self._is_type_token(tok):
            raise CWLangError(f"line {tok.line}: expected a type, got {tok.value!r}")
        self._next()
        return str(tok.value)

    def _finish_field(self, *, name: str, dtype: str, is_dram: bool) -> FieldDecl:
        line = self._peek().line
        dims: list[Node] = []
        if self._at("["):
            self._next()
            if not self._at("]"):
                dims = self._parse_arg_list("]")
            self._eat("]")
        init: Node | None = None
        if self._at("="):
            self._next()
            init = self._parse_expr()
        self._eat(";")
        return FieldDecl(name=name, dtype=dtype, dims=dims, init=init, is_dram=is_dram, line=line)

    def _parse_method(self, *, return_type: str | None, name: str | None = None) -> MethodDecl:
        if name is None:
            name = self._eat_ident()
        line = self._peek().line
        params = self._parse_params()
        body = self._parse_block()
        return MethodDecl(name=name, params=params, body=body, return_type=return_type, line=line)

    def _parse_function(self) -> FuncDecl:
        is_dram = False
        return_type = self._parse_type_name()
        name = self._eat_ident()
        line = self._peek().line
        params = self._parse_params()
        body = self._parse_block()
        _ = is_dram
        return FuncDecl(name=name, params=params, body=body, return_type=return_type, line=line)

    def _parse_params(self) -> list[Param]:
        self._eat("(")
        params: list[Param] = []
        while not self._at(")"):
            is_dram = False
            if self._at(")"):
                break
            if self._at("DRAM"):
                self._next()
                is_dram = True
            # Untyped param (common on magic methods): IDENT followed by ',' / ')'.
            if (
                not is_dram
                and self._peek().kind == "IDENT"
                and self._peek(1).kind == "OP"
                and self._peek(1).value in (",", ")")
            ):
                pname = self._eat_ident()
                params.append(Param(name=pname, dtype="auto", is_dram=False, is_array=False))
            else:
                dtype = self._parse_type_name()
                pname = self._eat_ident()
                is_array = False
                while self._at("["):
                    self._next()
                    if not self._at("]"):
                        self._parse_arg_list("]")
                    self._eat("]")
                    is_array = True
                params.append(Param(name=pname, dtype=dtype, is_dram=is_dram, is_array=is_array))
            if self._at(","):
                self._next()
        self._eat(")")
        return params

    def _parse_block(self) -> list[Node]:
        self._eat("{")
        stmts: list[Node] = []
        while not self._at("}"):
            stmts.append(self._parse_stmt())
        self._eat("}")
        return stmts

    # -- statements --
    def _stmt_starts_decl(self) -> bool:
        """A statement is a typed var-decl when it starts ``[DRAM] TYPE IDENT``."""
        idx = self.pos
        if self._peek().kind == "KW" and self._peek().value == "DRAM":
            idx += 1
        t0 = self.toks[idx] if idx < len(self.toks) else self._peek()
        t1 = self.toks[idx + 1] if idx + 1 < len(self.toks) else self._peek()
        # TYPE must be an identifier (dtype/alias/class) and be followed by an
        # identifier name (not '(' / '.' / operator) -> declaration.
        return t0.kind == "IDENT" and t1.kind == "IDENT"

    def _parse_stmt(self) -> Node:
        if self._at("{"):
            line = self._peek().line
            body = self._parse_block()
            return If(line=line, cond=Literal(line=line, value=1), then=body, otherwise=[])
        if self._at("return"):
            return self._parse_return()
        if self._at("if"):
            return self._parse_if()
        if self._at("for"):
            return self._parse_for()
        if self._at("DRAM") or self._stmt_starts_decl():
            return self._parse_var_decl()
        stmt = self._parse_simple_stmt()
        self._eat(";")
        return stmt

    def _parse_var_decl(self) -> VarDecl:
        is_dram = False
        if self._at("DRAM"):
            self._next()
            is_dram = True
        line = self._peek().line
        dtype = self._parse_type_name()
        name = self._eat_ident()
        dims: list[Node] = []
        if self._at("["):
            self._next()
            if not self._at("]"):
                dims = self._parse_arg_list("]")
            self._eat("]")
        init: Node | None = None
        if self._at("="):
            self._next()
            init = self._parse_expr()
        self._eat(";")
        return VarDecl(line=line, name=name, dtype=dtype, dims=dims, init=init, is_dram=is_dram)

    def _parse_return(self) -> Return:
        line = self._eat("return").line
        if self._at(";"):
            self._next()
            return Return(line=line, value=None)
        value = self._parse_expr()
        self._eat(";")
        return Return(line=line, value=value)

    def _parse_if(self) -> If:
        line = self._eat("if").line
        self._eat("(")
        cond = self._parse_expr()
        self._eat(")")
        then = self._parse_braced_or_single()
        otherwise: list[Node] = []
        if self._at("else"):
            self._next()
            otherwise = self._parse_braced_or_single()
        return If(line=line, cond=cond, then=then, otherwise=otherwise)

    def _parse_for(self) -> For:
        line = self._eat("for").line
        self._eat("(")
        init: Node | None = None
        if not self._at(";"):
            if self._at("DRAM") or self._stmt_starts_decl():
                init = self._parse_var_decl()  # consumes ';'
            else:
                init = self._parse_simple_stmt()
                self._eat(";")
        else:
            self._eat(";")
        cond: Node | None = None
        if not self._at(";"):
            cond = self._parse_expr()
        self._eat(";")
        update: Node | None = None
        if not self._at(")"):
            update = self._parse_simple_stmt()
        self._eat(")")
        body = self._parse_braced_or_single()
        return For(line=line, init=init, cond=cond, update=update, body=body)

    def _parse_braced_or_single(self) -> list[Node]:
        if self._at("{"):
            return self._parse_block()
        return [self._parse_stmt()]

    def _parse_simple_stmt(self) -> Node:
        """An assignment / ++ / -- / expression statement (no trailing ';')."""
        line = self._peek().line
        expr = self._parse_expr()
        if self._peek().kind == "OP" and self._peek().value in _ASSIGN_OPS:
            op = self._next().value
            value = self._parse_expr()
            return Assign(line=line, target=expr, op=op, value=value)
        if self._at("++") or self._at("--"):
            op = self._next().value
            delta = 1 if op == "++" else -1
            return Assign(
                line=line,
                target=expr,
                op="=",
                value=Binary(line=line, op="+", left=expr, right=Literal(line=line, value=delta)),
            )
        return ExprStmt(line=line, expr=expr)

    # -- expressions (precedence climbing) --
    def _parse_expr(self) -> Node:
        return self._parse_logic_or()

    def _parse_binary_level(self, sub: Callable[[], Node], ops: set[str]) -> Node:
        node = sub()
        while self._peek().kind == "OP" and self._peek().value in ops:
            op = self._next().value
            right = sub()
            node = Binary(line=node.line, op=op, left=node, right=right)
        return node

    def _parse_logic_or(self) -> Node:
        return self._parse_binary_level(self._parse_logic_and, {"||"})

    def _parse_logic_and(self) -> Node:
        return self._parse_binary_level(self._parse_equality, {"&&"})

    def _parse_equality(self) -> Node:
        return self._parse_binary_level(self._parse_relational, {"==", "!="})

    def _parse_relational(self) -> Node:
        return self._parse_binary_level(self._parse_additive, {"<", ">", "<=", ">="})

    def _parse_additive(self) -> Node:
        return self._parse_binary_level(self._parse_multiplicative, {"+", "-"})

    def _parse_multiplicative(self) -> Node:
        return self._parse_binary_level(self._parse_unary, {"*", "/", "%"})

    def _parse_unary(self) -> Node:
        if self._at("-") or self._at("!"):
            op = self._next()
            operand = self._parse_unary()
            return Unary(line=op.line, op=op.value, operand=operand)
        # C-style cast: ( TYPE ) operand, where TYPE is a dtype or alias.
        if (
            self._at("(")
            and self._peek(1).kind == "IDENT"
            and self._peek(2).kind == "OP"
            and self._peek(2).value == ")"
            and (self._peek(1).value in DTYPES or self._peek(1).value in self._alias_names)
        ):
            line = self._next().line  # '('
            type_name = self._next().value  # TYPE
            self._eat(")")
            operand = self._parse_unary()
            return Call(line=line, callee=Name(line=line, name=type_name), args=[operand])
        return self._parse_postfix()

    def _parse_postfix(self) -> Node:
        node = self._parse_primary()
        while True:
            if self._at("."):
                self._next()
                name = self._eat_ident()
                node = Member(line=node.line, obj=node, name=name)
            elif self._at("("):
                self._next()
                args = [] if self._at(")") else self._parse_arg_list(")")
                self._eat(")")
                node = Call(line=node.line, callee=node, args=args)
            elif self._at("["):
                self._next()
                args = self._parse_arg_list("]")
                self._eat("]")
                node = Index(line=node.line, obj=node, args=args)
            else:
                break
        return node

    def _parse_arg_list(self, closing: str) -> list[Node]:
        args = [self._parse_expr()]
        while self._at(","):
            self._next()
            if self._at(closing):
                break
            args.append(self._parse_expr())
        return args

    def _parse_primary(self) -> Node:
        tok = self._peek()
        if tok.kind == "INT" or tok.kind == "FLOAT":
            self._next()
            return Literal(line=tok.line, value=tok.value)
        if tok.kind == "STRING":
            self._next()
            return Literal(line=tok.line, value=tok.value)
        if tok.kind == "KW" and tok.value in ("true", "false"):
            self._next()
            return Literal(line=tok.line, value=1 if tok.value == "true" else 0)
        if tok.kind == "KW" and tok.value == "new":
            self._next()
            class_name = self._eat_ident()
            self._eat("(")
            args = [] if self._at(")") else self._parse_arg_list(")")
            self._eat(")")
            return New(line=tok.line, class_name=class_name, args=args)
        if self._at("("):
            self._next()
            inner = self._parse_expr()
            self._eat(")")
            return inner
        if tok.kind == "IDENT":
            self._next()
            return Name(line=tok.line, name=tok.value)
        raise CWLangError(f"line {tok.line}: unexpected token {tok.value!r}")


def parse(source: str) -> Program:
    return Parser(tokenize(source)).parse_program()


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------


class _ReturnSignal(Exception):
    def __init__(self, value: Any):
        self.value = value


class Instance:
    """A runtime object of a user-defined class/space."""

    def __init__(self, class_decl: ClassDecl):
        self.class_decl = class_decl
        self.fields: dict[str, Any] = {}

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Instance {self.class_decl.name} {self.fields}>"


class DramArray:
    """A sparse, dict-backed stand-in for a DRAM-bound tensor (default 0)."""

    def __init__(self, name: str):
        self.name = name
        self.store: dict[tuple, Any] = {}

    def get(self, key: tuple) -> Any:
        return self.store.get(key, 0)

    def set(self, key: tuple, value: Any) -> None:
        self.store[key] = value


class _System:
    """The builtin ``system`` object (compile-time host services)."""

    def bind_dram(self, name: str) -> DramArray:
        return DramArray(str(name))


_BINOP_MAGIC = {
    "+": "__add__",
    "-": "__sub__",
    "*": "__mul__",
    "/": "__div__",
    "%": "__mod__",
    "==": "__eq__",
    "!=": "__ne__",
    "<": "__lt__",
    ">": "__gt__",
    "<=": "__le__",
    ">=": "__ge__",
}


def _c_div(a: Any, b: Any) -> Any:
    if isinstance(a, int) and isinstance(b, int):
        if b == 0:
            return 0  # WAU div-by-zero convention
        q = abs(a) // abs(b)
        return -q if (a < 0) ^ (b < 0) else q
    if b == 0:
        return 0.0
    return a / b


def _c_mod(a: Any, b: Any) -> Any:
    if isinstance(a, int) and isinstance(b, int):
        if b == 0:
            return 0
        return a - _c_div(a, b) * b
    if b == 0:
        return 0.0
    return a - _c_div(a, b) * b


class Interpreter:
    def __init__(self, program: Program):
        self.program = program
        self.output: list[str] = []
        self.globals: dict[str, Any] = {"system": _System()}

    # -- public API --
    def run_main(self, args: list[Any] | None = None) -> Any:
        if "main" not in self.program.functions:
            raise CWLangError("no 'main' function to run")
        return self.call_function(self.program.functions["main"], args or [])

    def run(self, args: list[Any] | None = None) -> Any:
        """Run ``main()`` with active-interpreter management (so ``print`` of
        instances can reach ``__str__``)."""
        _ACTIVE_INTERP.append(self)
        try:
            return self.run_main(args)
        finally:
            _ACTIVE_INTERP.pop()

    def eval_expression(self, source: str) -> Any:
        """Evaluate a single ``.cw`` expression in the program's global scope."""
        parser = Parser(tokenize(source))
        node = parser._parse_expr()
        if parser._peek().kind != "EOF":
            raise CWLangError(f"line {parser._peek().line}: trailing tokens after expression")
        env = _Env(self, {}, None)
        _ACTIVE_INTERP.append(self)
        try:
            return env.eval(node)
        finally:
            _ACTIVE_INTERP.pop()

    def convert(self, value: Any, target_dtype: str) -> Any:
        """Convert ``value`` to ``target_dtype`` using class magic methods when
        ``value`` is an instance, otherwise a builtin numeric cast. This is the
        hook the rest of the toolchain uses for dynamic type-format conversion.
        """
        target = self._resolve_alias(target_dtype)
        if isinstance(value, Instance):
            return self._convert_instance(value, target)
        return self._cast_scalar(value, target)

    # -- function / method invocation --
    def call_function(self, func: FuncDecl, args: list[Any]) -> Any:
        scope = self._bind_params(func.params, args, func.line)
        return self._exec_body(func.body, scope, self_obj=None)

    def call_method(self, instance: Instance, method: MethodDecl, args: list[Any]) -> Any:
        scope = self._bind_params(method.params, args, method.line)
        return self._exec_body(method.body, scope, self_obj=instance)

    def _bind_params(self, params: list[Param], args: list[Any], line: int) -> dict[str, Any]:
        if len(args) != len(params):
            raise CWLangError(
                f"line {line}: expected {len(params)} argument(s), got {len(args)}"
            )
        return {p.name: a for p, a in zip(params, args)}

    def _exec_body(self, body: list[Node], scope: dict[str, Any], self_obj: Instance | None) -> Any:
        env = _Env(self, scope, self_obj)
        try:
            for stmt in body:
                env.exec(stmt)
        except _ReturnSignal as sig:
            return sig.value
        return None

    # -- object construction --
    def instantiate(self, class_decl: ClassDecl, args: list[Any]) -> Instance:
        inst = Instance(class_decl)
        # initialise fields (declaration order, in an env that can see fields
        # already set + globals) before running the constructor.
        init_env = _Env(self, {}, inst)
        for fld in class_decl.fields:
            inst.fields[fld.name] = self._default_field_value(fld, init_env)
        ctor = class_decl.methods.get("__init__")
        if ctor is not None:
            self.call_method(inst, ctor, args)
        elif args:
            raise CWLangError(
                f"line {class_decl.line}: class '{class_decl.name}' has no __init__ "
                f"but was constructed with {len(args)} argument(s)"
            )
        return inst

    def _default_field_value(self, fld: FieldDecl, env: "_Env") -> Any:
        if fld.init is not None:
            return env.eval(fld.init)
        if fld.is_dram:
            return DramArray(fld.name)
        if fld.dims:
            dims = [int(env.eval(d)) for d in fld.dims]
            return self._zero_array(dims, fld.dtype)
        return 0

    def _zero_array(self, dims: list[int], dtype: str) -> Any:
        if not dims:
            return self._zero_scalar(dtype)
        return [self._zero_array(dims[1:], dtype) for _ in range(dims[0])]

    def _zero_scalar(self, dtype: str) -> Any:
        resolved = self._resolve_alias(dtype)
        if resolved in self.program.classes:
            return self.instantiate(self.program.classes[resolved], [])
        if resolved in ("float", "float16", "bfloat16", "float32", "float64"):
            return 0.0
        return 0

    # -- conversion helpers --
    def _resolve_alias(self, name: str) -> str:
        seen = set()
        while name in self.program.aliases and name not in seen:
            seen.add(name)
            name = self.program.aliases[name].target
        return name

    def _convert_instance(self, value: Instance, target: str) -> Any:
        methods = value.class_decl.methods
        if "__convert__" in methods:
            return self.call_method(value, methods["__convert__"], [target])
        if target in ("int", "int8", "int16", "int32", "int64", "uint8", "uint16", "uint32"):
            if "__to_int__" in methods:
                return self.call_method(value, methods["__to_int__"], [])
        if target in ("float", "float16", "bfloat16", "float32", "float64"):
            if "__to_float__" in methods:
                return self.call_method(value, methods["__to_float__"], [])
        raise CWLangError(
            f"class '{value.class_decl.name}' has no conversion magic method for '{target}'"
        )

    def _cast_scalar(self, value: Any, target: str) -> Any:
        if target in ("float", "float16", "bfloat16", "float32", "float64"):
            return float(value)
        # integer casts truncate toward zero
        v = int(value) if not isinstance(value, int) else value
        return v


class _Env:
    """Execution environment: a single flat local scope plus the bound self."""

    def __init__(self, interp: Interpreter, scope: dict[str, Any], self_obj: Instance | None):
        self.i = interp
        self.scope = scope
        self.self_obj = self_obj

    # -- statements --
    def exec(self, node: Node) -> None:
        method = getattr(self, f"_exec_{type(node).__name__}", None)
        if method is None:
            raise CWLangError(f"line {node.line}: cannot execute {type(node).__name__}")
        method(node)

    def _exec_VarDecl(self, node: VarDecl) -> None:
        if node.init is not None:
            self.scope[node.name] = self.eval(node.init)
        elif node.is_dram:
            self.scope[node.name] = DramArray(node.name)
        elif node.dims:
            dims = [int(self.eval(d)) for d in node.dims]
            self.scope[node.name] = self.i._zero_array(dims, node.dtype)
        else:
            self.scope[node.name] = self.i._zero_scalar(node.dtype)

    def _exec_Assign(self, node: Assign) -> None:
        if node.op == "=":
            self._assign(node.target, self.eval(node.value))
            return
        current = self.eval(node.target)
        rhs = self.eval(node.value)
        op = node.op[0]
        self._assign(node.target, self._apply_binop(op, current, rhs, node.line))

    def _exec_Return(self, node: Return) -> None:
        raise _ReturnSignal(self.eval(node.value) if node.value is not None else None)

    def _exec_If(self, node: If) -> None:
        if _truthy(self.eval(node.cond)):
            self._exec_block(node.then)
        else:
            self._exec_block(node.otherwise)

    def _exec_For(self, node: For) -> None:
        if node.init is not None:
            self.exec(node.init) if isinstance(node.init, VarDecl) else self.exec(node.init)
        while node.cond is None or _truthy(self.eval(node.cond)):
            self._exec_block(node.body)
            if node.update is not None:
                self.exec(node.update)

    def _exec_ExprStmt(self, node: ExprStmt) -> None:
        self.eval(node.expr)

    def _exec_block(self, body: list[Node]) -> None:
        for stmt in body:
            self.exec(stmt)

    # -- assignment targets --
    def _assign(self, target: Node, value: Any) -> None:
        if isinstance(target, Name):
            if target.name in self.scope:
                self.scope[target.name] = value
            elif self.self_obj is not None and target.name in self.self_obj.fields:
                self.self_obj.fields[target.name] = value
            else:
                # default: create in local scope
                self.scope[target.name] = value
            return
        if isinstance(target, Member):
            obj = self.eval(target.obj)
            if isinstance(obj, Instance):
                obj.fields[target.name] = value
                return
            raise CWLangError(f"line {target.line}: cannot assign member of non-object")
        if isinstance(target, Index):
            obj = self.eval(target.obj)
            keys = [self.eval(a) for a in target.args]
            if isinstance(obj, DramArray):
                obj.set(tuple(keys), value)
                return
            self._set_nested(obj, keys, value, target.line)
            return
        raise CWLangError(f"line {target.line}: invalid assignment target")

    def _set_nested(self, container: Any, keys: list[Any], value: Any, line: int) -> None:
        for k in keys[:-1]:
            container = container[int(k)]
        container[int(keys[-1])] = value

    # -- expressions --
    def eval(self, node: Node) -> Any:
        method = getattr(self, f"_eval_{type(node).__name__}", None)
        if method is None:
            raise CWLangError(f"line {node.line}: cannot evaluate {type(node).__name__}")
        return method(node)

    def _eval_Literal(self, node: Literal) -> Any:
        return node.value

    def _eval_Name(self, node: Name) -> Any:
        if node.name in self.scope:
            return self.scope[node.name]
        if self.self_obj is not None and node.name in self.self_obj.fields:
            return self.self_obj.fields[node.name]
        if node.name in self.i.globals:
            return self.i.globals[node.name]
        if node.name in self.program_constants():
            return self.program_constants()[node.name]
        raise CWLangError(f"line {node.line}: undefined name '{node.name}'")

    def program_constants(self) -> dict[str, Any]:
        return {}

    def _eval_Member(self, node: Member) -> Any:
        obj = self.eval(node.obj)
        if isinstance(obj, list):
            if node.name == "count":
                return len(obj)
            raise CWLangError(f"line {node.line}: arrays have no member '{node.name}'")
        if isinstance(obj, Instance):
            if node.name in obj.fields:
                return obj.fields[node.name]
            if node.name in obj.class_decl.methods:
                return _BoundMethod(self.i, obj, obj.class_decl.methods[node.name])
            raise CWLangError(
                f"line {node.line}: '{obj.class_decl.name}' has no member '{node.name}'"
            )
        if isinstance(obj, (DramArray, _System)):
            return _BoundBuiltin(obj, node.name, node.line)
        raise CWLangError(f"line {node.line}: cannot access member '{node.name}'")

    def _eval_Index(self, node: Index) -> Any:
        obj = self.eval(node.obj)
        keys = [self.eval(a) for a in node.args]
        if isinstance(obj, DramArray):
            return obj.get(tuple(keys))
        for k in keys:
            obj = obj[int(k)]
        return obj

    def _eval_New(self, node: New) -> Any:
        name = self.i._resolve_alias(node.class_name)
        if name not in self.program.classes:
            raise CWLangError(f"line {node.line}: unknown class '{node.class_name}'")
        args = [self.eval(a) for a in node.args]
        return self.i.instantiate(self.program.classes[name], args)

    def _eval_Unary(self, node: Unary) -> Any:
        v = self.eval(node.operand)
        if node.op == "-":
            if isinstance(v, Instance) and "__neg__" in v.class_decl.methods:
                return self.i.call_method(v, v.class_decl.methods["__neg__"], [])
            return -v
        return 0 if _truthy(v) else 1

    def _eval_Binary(self, node: Binary) -> Any:
        if node.op == "&&":
            return 1 if (_truthy(self.eval(node.left)) and _truthy(self.eval(node.right))) else 0
        if node.op == "||":
            return 1 if (_truthy(self.eval(node.left)) or _truthy(self.eval(node.right))) else 0
        return self._apply_binop(node.op, self.eval(node.left), self.eval(node.right), node.line)

    def _apply_binop(self, op: str, left: Any, right: Any, line: int) -> Any:
        if isinstance(left, Instance):
            magic = _BINOP_MAGIC.get(op)
            if magic and magic in left.class_decl.methods:
                return self.i.call_method(left, left.class_decl.methods[magic], [right])
            raise CWLangError(
                f"line {line}: class '{left.class_decl.name}' has no magic method for '{op}'"
            )
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            return _c_div(left, right)
        if op == "%":
            return _c_mod(left, right)
        if op == "==":
            return 1 if left == right else 0
        if op == "!=":
            return 1 if left != right else 0
        if op == "<":
            return 1 if left < right else 0
        if op == ">":
            return 1 if left > right else 0
        if op == "<=":
            return 1 if left <= right else 0
        if op == ">=":
            return 1 if left >= right else 0
        raise CWLangError(f"line {line}: unknown operator '{op}'")

    def _eval_Call(self, node: Call) -> Any:
        callee = node.callee
        args = [self.eval(a) for a in node.args]

        # builtin dtype cast: TYPE(x)
        if isinstance(callee, Name):
            resolved = self.i._resolve_alias(callee.name)
            if callee.name == "print":
                text = " ".join(_stringify(a) for a in args)
                self.i.output.append(text)
                return None
            if resolved in DTYPES:
                if len(args) != 1:
                    raise CWLangError(f"line {node.line}: cast {callee.name}() takes 1 argument")
                return self.i.convert(args[0], resolved)
            if callee.name in self.program.functions:
                return self.i.call_function(self.program.functions[callee.name], args)
            # method on the current self (bare call inside a method)
            if self.self_obj is not None and callee.name in self.self_obj.class_decl.methods:
                return self.i.call_method(
                    self.self_obj, self.self_obj.class_decl.methods[callee.name], args
                )
            raise CWLangError(f"line {node.line}: unknown function '{callee.name}'")

        target = self.eval(callee)
        if isinstance(target, _BoundMethod):
            return self.i.call_method(target.instance, target.method, args)
        if isinstance(target, _BoundBuiltin):
            return target.invoke(args)
        raise CWLangError(f"line {node.line}: value is not callable")

    @property
    def program(self) -> Program:
        return self.i.program


@dataclass
class _BoundMethod:
    interp: Interpreter
    instance: Instance
    method: MethodDecl


class _BoundBuiltin:
    def __init__(self, obj: Any, name: str, line: int):
        self.obj = obj
        self.name = name
        self.line = line

    def invoke(self, args: list[Any]) -> Any:
        fn = getattr(self.obj, self.name, None)
        if fn is None or not callable(fn):
            raise CWLangError(f"line {self.line}: no builtin method '{self.name}'")
        return fn(*args)


def _truthy(value: Any) -> bool:
    if isinstance(value, Instance):
        return True
    return bool(value)


def _stringify(value: Any) -> str:
    if isinstance(value, Instance):
        if "__str__" in value.class_decl.methods:
            interp = _ACTIVE_INTERP[-1] if _ACTIVE_INTERP else None
            if interp is not None:
                return str(interp.call_method(value, value.class_decl.methods["__str__"], []))
        return f"<{value.class_decl.name}>"
    if isinstance(value, float):
        return repr(value)
    return str(value)


_ACTIVE_INTERP: list[Interpreter] = []


def run_program(source: str, *, main_args: list[Any] | None = None) -> tuple[Any, list[str]]:
    """Parse + interpret a ``.cw`` program, running ``main()``. Returns the
    ``main`` return value and any captured ``print`` output."""
    program = parse(source)
    interp = Interpreter(program)
    _ACTIVE_INTERP.append(interp)
    try:
        result = interp.run_main(main_args)
    finally:
        _ACTIVE_INTERP.pop()
    return result, interp.output


def load_program(path: Path) -> Program:
    return parse(Path(path).read_text())
