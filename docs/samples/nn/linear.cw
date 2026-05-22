// WAU sample: Linear (fully-connected) layer with bias and ReLU.
// Computes  y[b, o] = relu( sum_i x[b, i] * W[o, i] + bias[o] )
// Tiled over batch and output features; lanes parallelize output features.
//
// This sample also demonstrates the new `alias` syntax — a compile-time
// typedef. Changing the right-hand side of an alias retargets every
// variable that uses it to a new precision, without touching individual
// declarations. The body of an alias declaration is reserved for future
// compiler-side "magic methods" that govern how this type interoperates
// with other types (promotion rules between float16/float32/bfloat16,
// fused MAC hints, scalar/vector lowering, etc.). For now an alias is
// purely a textual substitution at compile time.
// @wau lane_parallelism=4
// @wau max_in_flight=4
// @wau preferred_dtype=float32
// @wau placement_policy=balance
// @wau lowering_profile=throughput_optimized
// @wau program_priority=4
// @wau program_load_balance=least_busy

// --- Type aliases ----------------------------------------------------------
// Working/storage precision for activations and weights. Swap to float16
// or bfloat16 here to retarget the whole layer without further edits.
alias real  = float32;
// Accumulator precision; kept separate so a low-precision storage layout
// (e.g. float16) can still accumulate in float32.
alias accum = float32;
// Index/loop-counter type; kept aliased so a future move to int16 indices
// on small models doesn't ripple through every loop.
alias index = int32;

void main() {
    index B   = 64;    // batch size
    index IN  = 512;   // input features
    index OUT = 256;   // output features

    DRAM real input_acts[]  = system.bind_dram("linear_input");
    DRAM real weights[]     = system.bind_dram("linear_weights");
    DRAM real bias[]        = system.bind_dram("linear_bias");
    DRAM real output_acts[] = system.bind_dram("linear_output");

    linear_layer layer = new linear_layer();
    layer.configure(input_acts, weights, bias, output_acts, B, IN, OUT);
    layer.run();
}

space linear_layer {
    // Tile shape.
    index BATCH_BLOCK = 16;
    index OUT_BLOCK   = 16;
    index IN_BLOCK    = 32;

    // DRAM-backed tensors.
    DRAM real x[];     // [B,   IN]
    DRAM real W[];     // [OUT, IN]
    DRAM real b[];     // [OUT]
    DRAM real y[];     // [B,   OUT]

    // Runtime dimensions.
    index B;
    index IN;
    index OUT;

    // On-chip working set. Two input buffers for ping/pong overlap
    // between DRAM load and compute.
    real  input_tile_ping[16, 32];     // [BATCH_BLOCK, IN_BLOCK]
    real  input_tile_pong[16, 32];
    real  weight_block[16, 32];        // [OUT_BLOCK,   IN_BLOCK]
    accum output_tile[16, 16];         // [BATCH_BLOCK, OUT_BLOCK]
    real  bias_block[16];

    // Shared execution state for worker spaces.
    index current_batch_base;
    index current_out_base;
    index current_in_base;
    index current_input_buffer;

    tile_loader loader;
    tile_writer writer;
    out_worker  workers[16];

    void configure(
        DRAM real input_acts[],
        DRAM real weights[],
        DRAM real bias_data[],
        DRAM real output_acts[],
        index batch,
        index in_features,
        index out_features
    ) {
        x = input_acts;
        W = weights;
        b = bias_data;
        y = output_acts;

        B   = batch;
        IN  = in_features;
        OUT = out_features;

        init();
    }

    void init() {
        for (index lane = 0; lane < workers.count; lane++) {
            workers[lane].set_lane(lane);
        }
    }

    void run() {
        index batch_tiles = ceil_div(B, BATCH_BLOCK);

        for (index oc = 0; oc < OUT; oc += OUT_BLOCK) {
            current_out_base = oc;
            loader.load_bias_block(current_out_base);

            for (index bt = 0; bt < batch_tiles; bt++) {
                current_batch_base = bt * BATCH_BLOCK;

                clear_output_tile();

                // Preload first input block for this batch tile.
                loader.load_input_block(0, current_batch_base, 0);

                for (index cb = 0; cb < IN; cb += IN_BLOCK) {
                    current_in_base      = cb;
                    current_input_buffer = (cb / IN_BLOCK) % 2;

                    index next_cb = cb + IN_BLOCK;
                    if (next_cb < IN) {
                        // Non-blocking prefetch into the unused ping/pong slot.
                        loader.prefetch_input_block(
                            1 - current_input_buffer,
                            current_batch_base,
                            next_cb
                        );
                    }

                    loader.load_weight_block(current_out_base, current_in_base);

                    // Independent per output-channel lane.
                    for (index lane = 0; lane < workers.count; lane++) {
                        workers[lane].accumulate_current_block();
                    }
                }

                // Independent per lane.
                for (index lane = 0; lane < workers.count; lane++) {
                    workers[lane].finalize_bias_relu();
                }

                writer.store_output_tile(current_out_base, current_batch_base);
            }
        }
    }

    void clear_output_tile() {
        for (index bi = 0; bi < BATCH_BLOCK; bi++) {
            for (index oc = 0; oc < OUT_BLOCK; oc++) {
                output_tile[bi, oc] = 0.0f;
            }
        }
    }

    index ceil_div(index a, index b) {
        return (a + b - 1) / b;
    }

    index x_index(index batch_i, index in_feat) {
        return batch_i * IN + in_feat;
    }

    index y_index(index batch_i, index out_feat) {
        return batch_i * OUT + out_feat;
    }

    index w_index(index out_feat, index in_feat) {
        return out_feat * IN + in_feat;
    }

    space tile_loader {
        void load_bias_block(index oc_base) {
            for (index oc = 0; oc < OUT_BLOCK; oc++) {
                index goc = oc_base + oc;
                if (goc < OUT) {
                    bias_block[oc] = b[goc];
                } else {
                    bias_block[oc] = 0.0f;
                }
            }
        }

        void prefetch_input_block(index buffer_id, index batch_base, index in_base) {
            // Same data movement as load_input_block; scheduler may issue
            // this ahead of compute.
            load_input_block(buffer_id, batch_base, in_base);
        }

        void load_input_block(index buffer_id, index batch_base, index in_base) {
            for (index bi = 0; bi < BATCH_BLOCK; bi++) {
                for (index ic = 0; ic < IN_BLOCK; ic++) {
                    index gb  = batch_base + bi;
                    index gic = in_base + ic;

                    real v = 0.0f;
                    if (gb < B && gic < IN) {
                        v = x[x_index(gb, gic)];
                    }

                    if (buffer_id == 0) {
                        input_tile_ping[bi, ic] = v;
                    } else {
                        input_tile_pong[bi, ic] = v;
                    }
                }
            }
        }

        void load_weight_block(index oc_base, index in_base) {
            for (index oc = 0; oc < OUT_BLOCK; oc++) {
                for (index ic = 0; ic < IN_BLOCK; ic++) {
                    index goc = oc_base + oc;
                    index gic = in_base  + ic;

                    if (goc < OUT && gic < IN) {
                        weight_block[oc, ic] = W[w_index(goc, gic)];
                    } else {
                        weight_block[oc, ic] = 0.0f;
                    }
                }
            }
        }
    }

    space tile_writer {
        void store_output_tile(index oc_base, index batch_base) {
            for (index bi = 0; bi < BATCH_BLOCK; bi++) {
                index gb = batch_base + bi;
                if (gb < B) {
                    for (index oc = 0; oc < OUT_BLOCK; oc++) {
                        index goc = oc_base + oc;
                        if (goc < OUT) {
                            y[y_index(gb, goc)] = output_tile[bi, oc];
                        }
                    }
                }
            }
        }
    }

    space out_worker {
        index lane;

        void set_lane(index lane_id) {
            lane = lane_id;
        }

        void accumulate_current_block() {
            index goc = current_out_base + lane;
            if (goc >= OUT) {
                return;
            }

            for (index bi = 0; bi < BATCH_BLOCK; bi++) {
                accum sum = output_tile[bi, lane];

                for (index ic = 0; ic < IN_BLOCK; ic++) {
                    real in_v = 0.0f;
                    if (current_input_buffer == 0) {
                        in_v = input_tile_ping[bi, ic];
                    } else {
                        in_v = input_tile_pong[bi, ic];
                    }

                    real w_v = weight_block[lane, ic];
                    sum = sum + (in_v * w_v);
                }

                output_tile[bi, lane] = sum;
            }
        }

        void finalize_bias_relu() {
            index goc = current_out_base + lane;
            if (goc >= OUT) {
                return;
            }

            for (index bi = 0; bi < BATCH_BLOCK; bi++) {
                accum v = output_tile[bi, lane];
                v = v + bias_block[lane];

                if (v < 0.0f) {
                    v = 0.0f;
                }

                output_tile[bi, lane] = v;
            }
        }
    }
}
