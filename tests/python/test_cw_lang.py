# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

"""Tests for the real `.cw` front-end (lexer -> parser -> interpreter) in
`waugen.cw_lang`, focused on the class + magic-method type-conversion feature.
"""
from __future__ import annotations

from pathlib import Path
import unittest

from waugen.cw_lang import (
    CWLangError,
    Interpreter,
    parse,
    run_program,
    tokenize,
)


FIXED_POINT = r"""
class q8_8 {
    int32 raw;
    __init__(v) { raw = v; }
    __add__(o) { return new q8_8(raw + o.raw); }
    __mul__(o) { return new q8_8((raw * o.raw) / 256); }
    __sub__(o) { return new q8_8(raw - o.raw); }
    __lt__(o) { return raw < o.raw; }
    __eq__(o) { return raw == o.raw; }
    __neg__() { return new q8_8(0 - raw); }
    __to_float__() { return raw / 256.0; }
    __to_int__() { return raw / 256; }
    __convert__(target) {
        if (target == "float32") { return raw / 256.0; }
        if (target == "int32") { return raw / 256; }
        return raw;
    }
    __str__() { return raw; }
}
"""


class LexerTests(unittest.TestCase):
    def test_number_forms(self) -> None:
        kinds = [(t.kind, t.value) for t in tokenize("3 3.0 3.0f 1.0e-5 .5 42")[:-1]]
        self.assertEqual(kinds[0], ("INT", 3))
        self.assertEqual(kinds[1], ("FLOAT", 3.0))
        self.assertEqual(kinds[2], ("FLOAT", 3.0))
        self.assertEqual(kinds[3], ("FLOAT", 1.0e-5))
        self.assertEqual(kinds[4], ("FLOAT", 0.5))
        self.assertEqual(kinds[5], ("INT", 42))

    def test_comments_and_strings(self) -> None:
        toks = tokenize('// line\n/* block */ "hi\\n" x')
        self.assertEqual(toks[0].kind, "STRING")
        self.assertEqual(toks[0].value, "hi\n")
        self.assertEqual(toks[1].value, "x")


class ParserTests(unittest.TestCase):
    def test_all_repo_samples_parse(self) -> None:
        samples = [
            "docs/example-program.cw",
            "docs/samples/nn/linear.cw",
            "docs/samples/nn/gru.cw",
            "docs/samples/nn/transformer.cw",
            "docs/samples/types/fixed_point.cw",
            "demo/de0-nano/basic-example/host/programs/basic_arithmetic.cw",
        ]
        for s in samples:
            with self.subTest(sample=s):
                prog = parse(Path(s).read_text())
                self.assertTrue(prog.functions or prog.classes)

    def test_alias_and_class_collection(self) -> None:
        prog = parse("alias real = float32;\nclass C { int32 x; }\nvoid main(){}")
        self.assertEqual(prog.aliases["real"].target, "float32")
        self.assertIn("C", prog.classes)
        self.assertIn("main", prog.functions)


class InterpreterCoreTests(unittest.TestCase):
    def test_arithmetic_control_flow_and_functions(self) -> None:
        src = """
        int32 ceil_div(int32 a, int32 b) { return (a + b - 1) / b; }
        int32 main() {
            int32 acc = 0;
            for (int32 i = 1; i <= 4; i++) { acc += i; }
            if (acc > 5) { acc = acc + ceil_div(7, 2); }
            return acc;
        }
        """
        result, _ = run_program(src)
        self.assertEqual(result, 14)  # 10 + ceil_div(7,2)=4

    def test_c_integer_division_truncates_toward_zero(self) -> None:
        src = "int32 main() { return (0 - 7) / 2; }"
        result, _ = run_program(src)
        self.assertEqual(result, -3)

    def test_div_by_zero_is_zero(self) -> None:
        src = "int32 main() { return 5 / 0; }"
        result, _ = run_program(src)
        self.assertEqual(result, 0)

    def test_arrays_and_count(self) -> None:
        src = """
        int32 main() {
            int32 a[3, 2];
            a[1, 1] = 7;
            a[2, 0] = 5;
            return a[1, 1] + a[2, 0] + a.count;
        }
        """
        result, _ = run_program(src)
        self.assertEqual(result, 7 + 5 + 3)


class MagicMethodTests(unittest.TestCase):
    def _interp(self) -> Interpreter:
        return Interpreter(parse(FIXED_POINT))

    def test_operator_overloading(self) -> None:
        src = FIXED_POINT + """
        int32 main() {
            q8_8 a = new q8_8(256);
            q8_8 b = new q8_8(128);
            q8_8 c = a + b;
            q8_8 d = a * b;
            q8_8 e = a - b;
            return c.raw * 1000000 + d.raw * 1000 + e.raw;
        }
        """
        result, _ = run_program(src)
        self.assertEqual(result, 384 * 1000000 + 128 * 1000 + 128)

    def test_comparison_and_neg(self) -> None:
        src = FIXED_POINT + """
        int32 main() {
            q8_8 a = new q8_8(256);
            q8_8 b = new q8_8(128);
            int32 lt = 0;
            if (b < a) { lt = 1; }
            q8_8 n = -a;
            return lt * 10000 + (0 - n.raw);
        }
        """
        result, _ = run_program(src)
        self.assertEqual(result, 1 * 10000 + 256)

    def test_builtin_cast_dispatches_to_conversion_magic(self) -> None:
        src = FIXED_POINT + """
        float32 main() {
            q8_8 a = new q8_8(384);
            return float32(a);
        }
        """
        result, _ = run_program(src)
        self.assertEqual(result, 1.5)

    def test_convert_api_instance(self) -> None:
        interp = self._interp()
        inst = interp.instantiate(interp.program.classes["q8_8"], [512])
        self.assertEqual(interp.convert(inst, "float32"), 2.0)
        self.assertEqual(interp.convert(inst, "int32"), 2)

    def test_convert_api_generic_dispatch(self) -> None:
        # __convert__ wins over __to_*__ when present (generic dynamic dispatch)
        interp = self._interp()
        inst = interp.instantiate(interp.program.classes["q8_8"], [384])
        self.assertEqual(interp.eval_expression("new q8_8(384)").fields["raw"], 384)
        self.assertEqual(interp.convert(inst, "float32"), 1.5)

    def test_convert_scalar_cast(self) -> None:
        interp = self._interp()
        self.assertEqual(interp.convert(3.9, "int32"), 3)
        self.assertEqual(interp.convert(3, "float32"), 3.0)

    def test_alias_resolves_for_cast_and_convert(self) -> None:
        src = "alias real = float32;\n" + FIXED_POINT
        interp = Interpreter(parse(src))
        inst = interp.instantiate(interp.program.classes["q8_8"], [256])
        self.assertEqual(interp.convert(inst, "real"), 1.0)


class ErrorTests(unittest.TestCase):
    def test_undefined_name(self) -> None:
        with self.assertRaises(CWLangError):
            run_program("int32 main() { return missing; }")

    def test_missing_ctor_with_args(self) -> None:
        with self.assertRaises(CWLangError):
            run_program("class C { int32 x; }\nint32 main() { C c = new C(5); return 0; }")

    def test_no_conversion_method(self) -> None:
        interp = Interpreter(parse("class C { int32 x; __init__() { x = 1; } }\nvoid main(){}"))
        inst = interp.instantiate(interp.program.classes["C"], [])
        with self.assertRaises(CWLangError):
            interp.convert(inst, "float32")

    def test_operator_without_magic_method(self) -> None:
        src = "class C { int32 x; __init__(){ x = 1; } }\nint32 main(){ C a = new C(); C b = new C(); C c = a + b; return 0; }"
        with self.assertRaises(CWLangError):
            run_program(src)


if __name__ == "__main__":
    unittest.main()
