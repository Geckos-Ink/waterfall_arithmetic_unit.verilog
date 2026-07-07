// WAU sample: compile-time type classes with magic methods.
//
// This program is meant to be executed by the *host-side* `.cw` interpreter
// (`python -m waugen cw-eval`), NOT lowered onto the WAU grid. It shows how a
// class can describe a custom numeric format and the conversions/arithmetic
// that "should not run on the WAU" -- exactly the kind of complex type-format
// handling the compiler needs to do automatically when bridging precisions.
//
// Magic methods used here:
//   __init__         constructor invoked by `new q8_8(raw)`
//   __add__/__mul__  operator overloading (a + b -> a.__add__(b))
//   __lt__           comparison overloading
//   __to_float__     conversion hook used by float32(x) and convert(x,"float32")
//   __to_int__       conversion hook used by int32(x)
//   __convert__      generic dynamic conversion dispatch by dtype name
//   __str__          textual form used by print(...)

// A Q8.8 signed fixed-point number stored as a raw int (value = raw / 256).
class q8_8 {
    int32 raw;

    __init__(value) {
        raw = value;
    }

    // Build a q8_8 from a real number (compiler-side, rounds toward zero).
    __from_float__(f) {
        return new q8_8(int32(f * 256.0));
    }

    __add__(other) {
        return new q8_8(raw + other.raw);
    }

    __mul__(other) {
        // fixed-point multiply: (a/256) * (b/256) = (a*b/256) / 256
        return new q8_8((raw * other.raw) / 256);
    }

    __lt__(other) {
        return raw < other.raw;
    }

    __to_float__() {
        return raw / 256.0;
    }

    __to_int__() {
        return raw / 256;
    }

    // Generic conversion dispatch: the compiler can ask for any target dtype
    // by name and this method decides how to satisfy it.
    __convert__(target) {
        if (target == "float32") {
            return raw / 256.0;
        }
        if (target == "int32") {
            return raw / 256;
        }
        // default: return the raw backing store
        return raw;
    }

    __str__() {
        return raw;
    }
}

int32 main() {
    q8_8 one  = new q8_8(256);   // 1.0
    q8_8 half = new q8_8(128);   // 0.5

    q8_8 sum  = one + half;      // 1.5  -> raw 384
    q8_8 prod = one * half;      // 0.5  -> raw 128

    print("sum.raw", sum.raw, "as float32", float32(sum));
    print("prod.raw", prod.raw, "as float32", float32(prod));

    if (half < one) {
        print("ordering: half < one");
    }

    // Dynamic, name-driven conversion (what the compiler calls internally).
    print("convert sum -> int32:", sum.__convert__("int32"));

    return sum.raw;
}
