// WAU basic-example kernel for DE0-Nano (EP4CE22, 22k LEs).
//
// The compile-cw frontend currently lowers a conv2d+residual+relu kernel
// template — we use it here with the smallest legal dimensions so the
// compiled flow stays inside the DE0-Nano LE budget while still exercising
// the full add/mul/sub/max/div path through the WAU grid.
//
// Compiled flow_id and program_id are set on the command line via
// `python -m waugen compile-cw --flow-id 90 --program-id 90 ...`
// (see scripts/build.ps1 -- the `-WithCw` switch).
//
// Tuning pragmas chosen for the small 3x2 grid:
// @wau lane_parallelism=2
// @wau max_in_flight=2
// @wau preferred_dtype=int32
// @wau placement_policy=balance
// @wau lowering_profile=throughput_optimized
// @wau program_priority=3
// @wau program_load_balance=least_busy

void main() {
    int32 H = 4;
    int32 W = 4;
    int32 Cin = 4;
    int32 Cout = 4;

    DRAM int32 input_feature_map[]  = system.bind_dram("ifmap");
    DRAM int32 kernel_weights[]     = system.bind_dram("weights_3x3");
    DRAM int32 bias[]               = system.bind_dram("bias");
    DRAM int32 residual_map[]       = system.bind_dram("residual");
    DRAM int32 output_feature_map[] = system.bind_dram("ofmap");

    micro_conv2d_kernel kernel = new micro_conv2d_kernel();
    kernel.configure(
        input_feature_map,
        kernel_weights,
        bias,
        residual_map,
        output_feature_map,
        H, W, Cin, Cout
    );
    kernel.run();
}

space micro_conv2d_kernel {
    int32 K = 3;
    int32 TILE_H = 4;
    int32 TILE_W = 4;
    int32 CIN_BLOCK = 2;
    int32 COUT_BLOCK = 2;

    DRAM int32 ifmap[];
    DRAM int32 w3x3[];
    DRAM int32 bias[];
    DRAM int32 residual[];
    DRAM int32 ofmap[];

    int32 H;
    int32 W;
    int32 Cin;
    int32 Cout;

    int32 input_tile_ping[6, 6, 2];
    int32 input_tile_pong[6, 6, 2];
    int32 weight_block[3, 3, 2, 2];
    int32 output_tile[4, 4, 2];
    int32 bias_block[2];

    int32 current_tile_y;
    int32 current_tile_x;
    int32 current_cin_base;
    int32 current_oc_base;
    int32 current_input_buffer;

    tile_loader loader;
    tile_writer writer;
    channel_worker workers[2];

    void configure(
        DRAM int32 input_feature_map[],
        DRAM int32 kernel_weights[],
        DRAM int32 bias_data[],
        DRAM int32 residual_map[],
        DRAM int32 output_feature_map[],
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

                    loader.load_input_block(0, current_tile_y, current_tile_x, 0);

                    for (int32 cb = 0; cb < Cin; cb += CIN_BLOCK) {
                        current_cin_base = cb;
                        current_input_buffer = (cb / CIN_BLOCK) % 2;

                        int32 next_cb = cb + CIN_BLOCK;
                        if (next_cb < Cin) {
                            loader.prefetch_input_block(
                                1 - current_input_buffer,
                                current_tile_y, current_tile_x, next_cb
                            );
                        }

                        loader.load_weight_block(current_oc_base, current_cin_base);

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

    void clear_output_tile() {
        for (int32 oy = 0; oy < TILE_H; oy++) {
            for (int32 ox = 0; ox < TILE_W; ox++) {
                for (int32 oc = 0; oc < COUT_BLOCK; oc++) {
                    output_tile[oy, ox, oc] = 0;
                }
            }
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

    space tile_loader {
        void load_bias_block(int32 oc_base) {
            for (int32 oc = 0; oc < COUT_BLOCK; oc++) {
                int32 goc = oc_base + oc;
                if (goc < Cout) {
                    bias_block[oc] = bias[goc];
                } else {
                    bias_block[oc] = 0;
                }
            }
        }

        void prefetch_input_block(int32 buffer_id, int32 tile_y, int32 tile_x, int32 cin_base) {
            load_input_block(buffer_id, tile_y, tile_x, cin_base);
        }

        void load_input_block(int32 buffer_id, int32 tile_y, int32 tile_x, int32 cin_base) {
            for (int32 iy = 0; iy < TILE_H + 2; iy++) {
                for (int32 ix = 0; ix < TILE_W + 2; ix++) {
                    for (int32 ic = 0; ic < CIN_BLOCK; ic++) {
                        int32 gy = tile_y * TILE_H + iy - 1;
                        int32 gx = tile_x * TILE_W + ix - 1;
                        int32 gic = cin_base + ic;

                        int32 v = 0;
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
                                weight_block[ky, kx, ic, oc] = 0;
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
                    int32 sum = output_tile[oy, ox, lane];

                    for (int32 ky = 0; ky < K; ky++) {
                        for (int32 kx = 0; kx < K; kx++) {
                            for (int32 ic = 0; ic < CIN_BLOCK; ic++) {
                                int32 in_v = 0;
                                if (current_input_buffer == 0) {
                                    in_v = input_tile_ping[oy + ky, ox + kx, ic];
                                } else {
                                    in_v = input_tile_pong[oy + ky, ox + kx, ic];
                                }
                                int32 w_v = weight_block[ky, kx, ic, lane];
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
                        int32 v = output_tile[oy, ox, lane];
                        v = v + bias_block[lane];
                        v = v + residual[idx];
                        if (v < 0) {
                            v = 0;
                        }
                        output_tile[oy, ox, lane] = v;
                    }
                }
            }
        }
    }
}
