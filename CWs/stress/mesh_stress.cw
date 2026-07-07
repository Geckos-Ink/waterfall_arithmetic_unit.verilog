// WAU hardware mesh-stress kernel (.cw)
//
// Unlike CWs/example-program.cw -- which is a large Conv2D reference kept
// deliberately complex to stress the *compiler* front-end -- this kernel is
// purpose-built to stress the *generated hardware*: it maximizes sustained
// mesh/coordinator traffic on the small grids a DE0-Nano (EP4CE22, ~20k LE)
// can actually fit (2x2 .. 2x4).
//
// How it stresses the mesh once lowered by `compile-cw`:
//   - 8 channel workers + lane_parallelism=8 fan the compute out across every
//     core of a 2x4 grid (and every core of a 2x2 when the grid is smaller).
//   - tiny 8x8 tiles over a 256x256x256 tensor drive the lowering's recurrence
//     hint to its cap (32 iterations) -> the longest schedule the compiler
//     emits, so results keep bouncing across the mesh for many iterations.
//   - prefetch + ping/pong double buffering + residual + ReLU light up every
//     data-movement path in lowering, so load/compute/store all contend for
//     the highway routers at once.
//   - throughput_optimized + balance widen the per-node candidate-core set, so
//     the scheduler spreads work rather than pinning it to a hot core.
//
// It is still a genuine, self-consistent Conv2D + bias + residual + ReLU
// kernel (validated against waugen.cw_reference on the board), just leaner and
// tuned for saturation instead of front-end breadth.
//
// @wau lane_parallelism=8
// @wau max_in_flight=2
// @wau preferred_dtype=float32
// @wau placement_policy=balance
// @wau lowering_profile=throughput_optimized
// @wau program_priority=4
// @wau program_load_balance=least_busy

void main() {
    int32 H = 256;
    int32 W = 256;
    int32 Cin = 256;
    int32 Cout = 256;

    DRAM float32 input_feature_map[] = system.bind_dram("ifmap");
    DRAM float32 kernel_weights[] = system.bind_dram("weights_3x3");
    DRAM float32 bias[] = system.bind_dram("bias");
    DRAM float32 residual_map[] = system.bind_dram("residual");
    DRAM float32 output_feature_map[] = system.bind_dram("ofmap");

    mesh_stress_kernel kernel = new mesh_stress_kernel();
    kernel.configure(
        input_feature_map,
        kernel_weights,
        bias,
        residual_map,
        output_feature_map,
        H,
        W,
        Cin,
        Cout
    );
    kernel.run();
}

space mesh_stress_kernel {
    // Small tiles + many channels keep the recurrence hint saturated at 32.
    int32 K = 3;
    int32 TILE_H = 8;
    int32 TILE_W = 8;
    int32 CIN_BLOCK = 16;
    int32 COUT_BLOCK = 8;

    // DRAM-backed tensors (global memory).
    DRAM float32 ifmap[];
    DRAM float32 w3x3[];
    DRAM float32 bias[];
    DRAM float32 residual[];
    DRAM float32 ofmap[];

    // Runtime dimensions.
    int32 H;
    int32 W;
    int32 Cin;
    int32 Cout;

    // On-chip working set. Two input buffers ping/pong load vs compute.
    float32 input_tile_ping[10,10,16];
    float32 input_tile_pong[10,10,16];
    float32 weight_block[3,3,16,8];
    float32 output_tile[8,8,8];
    float32 bias_block[8];

    // Shared execution state for the worker spaces.
    int32 current_tile_y;
    int32 current_tile_x;
    int32 current_cin_base;
    int32 current_oc_base;
    int32 current_input_buffer;

    tile_loader loader;
    tile_writer writer;
    channel_worker workers[8];

    void configure(
        DRAM float32 input_feature_map[],
        DRAM float32 kernel_weights[],
        DRAM float32 bias_data[],
        DRAM float32 residual_map[],
        DRAM float32 output_feature_map[],
        int32 height,
        int32 width,
        int32 cin_count,
        int32 cout_count
    ) {
        ifmap = input_feature_map;
        w3x3 = kernel_weights;
        bias = bias_data;
        residual = residual_map;
        ofmap = output_feature_map;

        H = height;
        W = width;
        Cin = cin_count;
        Cout = cout_count;

        init();
    }

    void init() {
        for (int32 lane = 0; lane < workers.count; lane++) {
            workers[lane].set_lane(lane);
        }
    }

    int32 ceil_div(int32 a, int32 b) {
        return (a + b - 1) / b;
    }

    int32 ofmap_index(int32 y, int32 x, int32 c) {
        return ((y * W + x) * Cout) + c;
    }

    int32 ifmap_index(int32 y, int32 x, int32 c) {
        return ((y * W + x) * Cin) + c;
    }

    int32 weight_index(int32 oc, int32 ic, int32 ky, int32 kx) {
        return ((((oc * Cin) + ic) * K + ky) * K) + kx;
    }

    void clear_output_tile() {
        for (int32 oy = 0; oy < TILE_H; oy++) {
            for (int32 ox = 0; ox < TILE_W; ox++) {
                for (int32 oc = 0; oc < COUT_BLOCK; oc++) {
                    output_tile[oy, ox, oc] = 0.0f;
                }
            }
        }
    }

    void run() {
        int32 tiles_y = ceil_div(H, TILE_H);
        int32 tiles_x = ceil_div(W, TILE_W);

        for (int32 oc = 0; oc < Cout; oc += COUT_BLOCK) {
            current_oc_base = oc;
            loader.load_bias_block(current_oc_base);

            for (int32 ty = 0; ty < tiles_y; ty++) {
                for (int32 tx = 0; tx < tiles_x; tx++) {
                    current_tile_y = ty;
                    current_tile_x = tx;

                    clear_output_tile();

                    // Preload first input block for this tile.
                    loader.load_input_block(0, current_tile_y, current_tile_x, 0);

                    for (int32 cb = 0; cb < Cin; cb += CIN_BLOCK) {
                        current_cin_base = cb;
                        current_input_buffer = (cb / CIN_BLOCK) % 2;

                        int32 next_cb = cb + CIN_BLOCK;
                        if (next_cb < Cin) {
                            // Prefetch the next block into the other buffer while
                            // the workers consume the current block.
                            loader.prefetch_input_block(1 - current_input_buffer, current_tile_y, current_tile_x, next_cb);
                        }

                        loader.load_weight_block(current_oc_base, current_cin_base);

                        // Independent per output-channel lane; the compiler maps
                        // these to concurrent cores across the mesh.
                        for (int32 lane = 0; lane < workers.count; lane++) {
                            workers[lane].accumulate_current_block();
                        }
                    }

                    for (int32 lane = 0; lane < workers.count; lane++) {
                        workers[lane].finalize_bias_residual_relu();
                    }

                    writer.store_output_tile(current_oc_base, current_tile_y, current_tile_x);
                }
            }
        }
    }

    space tile_loader {
        void load_bias_block(int32 oc_base) {
            for (int32 oc = 0; oc < COUT_BLOCK; oc++) {
                int32 goc = oc_base + oc;
                if (goc < Cout) {
                    bias_block[oc] = bias[goc];
                } else {
                    bias_block[oc] = 0.0f;
                }
            }
        }

        void prefetch_input_block(int32 buffer_id, int32 tile_y, int32 tile_x, int32 cin_base) {
            // Same data movement as load_input_block; scheduler may issue ahead.
            load_input_block(buffer_id, tile_y, tile_x, cin_base);
        }

        void load_input_block(int32 buffer_id, int32 tile_y, int32 tile_x, int32 cin_base) {
            for (int32 iy = 0; iy < TILE_H + 2; iy++) {
                for (int32 ix = 0; ix < TILE_W + 2; ix++) {
                    for (int32 ic = 0; ic < CIN_BLOCK; ic++) {
                        int32 gy = tile_y * TILE_H + iy - 1;
                        int32 gx = tile_x * TILE_W + ix - 1;
                        int32 gic = cin_base + ic;

                        float32 v = 0.0f;
                        if (gy >= 0 && gy < H && gx >= 0 && gx < W && gic < Cin) {
                            v = ifmap[ifmap_index(gy, gx, gic)];
                        }

                        if (buffer_id == 0) {
                            input_tile_ping[iy, ix, ic] = v;
                        } else {
                            input_tile_pong[iy, ix, ic] = v;
                        }
                    }
                }
            }
        }

        void load_weight_block(int32 oc_base, int32 cin_base) {
            for (int32 ky = 0; ky < K; ky++) {
                for (int32 kx = 0; kx < K; kx++) {
                    for (int32 ic = 0; ic < CIN_BLOCK; ic++) {
                        for (int32 oc = 0; oc < COUT_BLOCK; oc++) {
                            int32 goc = oc_base + oc;
                            int32 gic = cin_base + ic;

                            if (goc < Cout && gic < Cin) {
                                weight_block[ky, kx, ic, oc] = w3x3[weight_index(goc, gic, ky, kx)];
                            } else {
                                weight_block[ky, kx, ic, oc] = 0.0f;
                            }
                        }
                    }
                }
            }
        }
    }

    space tile_writer {
        void store_output_tile(int32 oc_base, int32 tile_y, int32 tile_x) {
            for (int32 oy = 0; oy < TILE_H; oy++) {
                for (int32 ox = 0; ox < TILE_W; ox++) {
                    int32 gy = tile_y * TILE_H + oy;
                    int32 gx = tile_x * TILE_W + ox;

                    if (gy < H && gx < W) {
                        for (int32 oc = 0; oc < COUT_BLOCK; oc++) {
                            int32 goc = oc_base + oc;
                            if (goc < Cout) {
                                ofmap[ofmap_index(gy, gx, goc)] = output_tile[oy, ox, oc];
                            }
                        }
                    }
                }
            }
        }
    }

    space channel_worker {
        int32 lane;

        void set_lane(int32 lane_id) {
            lane = lane_id;
        }

        void accumulate_current_block() {
            int32 goc = current_oc_base + lane;
            if (goc >= Cout) {
                return;
            }

            for (int32 oy = 0; oy < TILE_H; oy++) {
                for (int32 ox = 0; ox < TILE_W; ox++) {
                    float32 sum = output_tile[oy, ox, lane];

                    for (int32 ky = 0; ky < K; ky++) {
                        for (int32 kx = 0; kx < K; kx++) {
                            for (int32 ic = 0; ic < CIN_BLOCK; ic++) {
                                float32 in_v = 0.0f;
                                if (current_input_buffer == 0) {
                                    in_v = input_tile_ping[oy + ky, ox + kx, ic];
                                } else {
                                    in_v = input_tile_pong[oy + ky, ox + kx, ic];
                                }

                                float32 w_v = weight_block[ky, kx, ic, lane];
                                sum = sum + (in_v * w_v);
                            }
                        }
                    }

                    output_tile[oy, ox, lane] = sum;
                }
            }
        }

        void finalize_bias_residual_relu() {
            int32 goc = current_oc_base + lane;
            if (goc >= Cout) {
                return;
            }

            for (int32 oy = 0; oy < TILE_H; oy++) {
                for (int32 ox = 0; ox < TILE_W; ox++) {
                    int32 gy = current_tile_y * TILE_H + oy;
                    int32 gx = current_tile_x * TILE_W + ox;

                    if (gy < H && gx < W) {
                        int32 idx = ofmap_index(gy, gx, goc);
                        float32 v = output_tile[oy, ox, lane];
                        v = v + bias_block[lane];
                        v = v + residual[idx];

                        if (v < 0.0f) {
                            v = 0.0f;
                        }

                        output_tile[oy, ox, lane] = v;
                    }
                }
            }
        }
    }
}
