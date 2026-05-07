// WAU reference program (proposed .cw syntax)
// Real-life kernel: tiled Conv2D + Bias + Residual + ReLU.
// Goal: provide compiler-oriented structure for concurrency and memory placement.
// @wau lane_parallelism=4
// @wau max_in_flight=4
// @wau preferred_dtype=float32

void main() {
    int H = 224;
    int W = 224;
    int Cin = 64;
    int Cout = 128;

    DRAM float32 input_feature_map[] = system.bind_dram("ifmap");
    DRAM float32 kernel_weights[] = system.bind_dram("weights_3x3");
    DRAM float32 bias[] = system.bind_dram("bias");
    DRAM float32 residual_map[] = system.bind_dram("residual");
    DRAM float32 output_feature_map[] = system.bind_dram("ofmap");

    conv2d_residual_kernel kernel = new conv2d_residual_kernel();
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

space conv2d_residual_kernel {
    // Kernel parameters.
    int K = 3;
    int TILE_H = 16;
    int TILE_W = 16;
    int CIN_BLOCK = 16;
    int COUT_BLOCK = 8;

    // DRAM-backed tensors (global memory).
    DRAM float32 ifmap[];
    DRAM float32 w3x3[];
    DRAM float32 bias[];
    DRAM float32 residual[];
    DRAM float32 ofmap[];

    // Runtime dimensions.
    int H;
    int W;
    int Cin;
    int Cout;

    // On-chip working set.
    // Two input buffers are used as ping/pong for overlap between load and compute.
    float32 input_tile_ping[18,18,16];
    float32 input_tile_pong[18,18,16];
    float32 weight_block[3,3,16,8];
    float32 output_tile[16,16,8];
    float32 bias_block[8];

    // Shared execution state for worker spaces.
    int current_tile_y;
    int current_tile_x;
    int current_cin_base;
    int current_oc_base;
    int current_input_buffer;

    tile_loader loader;
    tile_writer writer;
    channel_worker workers[8];

    void configure(
        DRAM float32 input_feature_map[],
        DRAM float32 kernel_weights[],
        DRAM float32 bias_data[],
        DRAM float32 residual_map[],
        DRAM float32 output_feature_map[],
        int height,
        int width,
        int cin_count,
        int cout_count
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
        for (int lane = 0; lane < workers.count; lane++) {
            workers[lane].set_lane(lane);
        }
    }

    void run() {
        int tiles_y = ceil_div(H, TILE_H);
        int tiles_x = ceil_div(W, TILE_W);

        for (int oc = 0; oc < Cout; oc += COUT_BLOCK) {
            current_oc_base = oc;
            loader.load_bias_block(current_oc_base);

            for (int ty = 0; ty < tiles_y; ty++) {
                for (int tx = 0; tx < tiles_x; tx++) {
                    current_tile_y = ty;
                    current_tile_x = tx;

                    clear_output_tile();

                    // Preload first input block for this tile.
                    loader.load_input_block(0, current_tile_y, current_tile_x, 0);

                    for (int cb = 0; cb < Cin; cb += CIN_BLOCK) {
                        current_cin_base = cb;
                        current_input_buffer = (cb / CIN_BLOCK) % 2;

                        int next_cb = cb + CIN_BLOCK;
                        if (next_cb < Cin) {
                            // Non-blocking intent: fetch next input block while workers consume current block.
                            loader.prefetch_input_block(1 - current_input_buffer, current_tile_y, current_tile_x, next_cb);
                        }

                        loader.load_weight_block(current_oc_base, current_cin_base);

                        // Independent per output-channel lane.
                        // Compiler can map these calls to concurrent cores.
                        for (int lane = 0; lane < workers.count; lane++) {
                            workers[lane].accumulate_current_block();
                        }
                    }

                    // Independent per lane, can be executed concurrently as well.
                    for (int lane = 0; lane < workers.count; lane++) {
                        workers[lane].finalize_bias_residual_relu();
                    }

                    writer.store_output_tile(current_oc_base, current_tile_y, current_tile_x);
                }
            }
        }
    }

    void clear_output_tile() {
        for (int oy = 0; oy < TILE_H; oy++) {
            for (int ox = 0; ox < TILE_W; ox++) {
                for (int oc = 0; oc < COUT_BLOCK; oc++) {
                    output_tile[oy, ox, oc] = 0.0f;
                }
            }
        }
    }

    int ceil_div(int a, int b) {
        return (a + b - 1) / b;
    }

    int ofmap_index(int y, int x, int c) {
        return ((y * W + x) * Cout) + c;
    }

    int ifmap_index(int y, int x, int c) {
        return ((y * W + x) * Cin) + c;
    }

    int weight_index(int oc, int ic, int ky, int kx) {
        return ((((oc * Cin) + ic) * K + ky) * K) + kx;
    }

    space tile_loader {
        void load_bias_block(int oc_base) {
            for (int oc = 0; oc < COUT_BLOCK; oc++) {
                int goc = oc_base + oc;
                if (goc < Cout) {
                    bias_block[oc] = bias[goc];
                } else {
                    bias_block[oc] = 0.0f;
                }
            }
        }

        void prefetch_input_block(int buffer_id, int tile_y, int tile_x, int cin_base) {
            // Same data movement as load_input_block; scheduler may issue this ahead of compute.
            load_input_block(buffer_id, tile_y, tile_x, cin_base);
        }

        void load_input_block(int buffer_id, int tile_y, int tile_x, int cin_base) {
            for (int iy = 0; iy < TILE_H + 2; iy++) {
                for (int ix = 0; ix < TILE_W + 2; ix++) {
                    for (int ic = 0; ic < CIN_BLOCK; ic++) {
                        int gy = tile_y * TILE_H + iy - 1;
                        int gx = tile_x * TILE_W + ix - 1;
                        int gic = cin_base + ic;

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

        void load_weight_block(int oc_base, int cin_base) {
            for (int ky = 0; ky < K; ky++) {
                for (int kx = 0; kx < K; kx++) {
                    for (int ic = 0; ic < CIN_BLOCK; ic++) {
                        for (int oc = 0; oc < COUT_BLOCK; oc++) {
                            int goc = oc_base + oc;
                            int gic = cin_base + ic;

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
        void store_output_tile(int oc_base, int tile_y, int tile_x) {
            for (int oy = 0; oy < TILE_H; oy++) {
                for (int ox = 0; ox < TILE_W; ox++) {
                    int gy = tile_y * TILE_H + oy;
                    int gx = tile_x * TILE_W + ox;

                    if (gy < H && gx < W) {
                        for (int oc = 0; oc < COUT_BLOCK; oc++) {
                            int goc = oc_base + oc;
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
        int lane;

        void set_lane(int lane_id) {
            lane = lane_id;
        }

        void accumulate_current_block() {
            int goc = current_oc_base + lane;
            if (goc >= Cout) {
                return;
            }

            for (int oy = 0; oy < TILE_H; oy++) {
                for (int ox = 0; ox < TILE_W; ox++) {
                    float32 sum = output_tile[oy, ox, lane];

                    for (int ky = 0; ky < K; ky++) {
                        for (int kx = 0; kx < K; kx++) {
                            for (int ic = 0; ic < CIN_BLOCK; ic++) {
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
            int goc = current_oc_base + lane;
            if (goc >= Cout) {
                return;
            }

            for (int oy = 0; oy < TILE_H; oy++) {
                for (int ox = 0; ox < TILE_W; ox++) {
                    int gy = current_tile_y * TILE_H + oy;
                    int gx = current_tile_x * TILE_W + ox;

                    if (gy < H && gx < W) {
                        int idx = ofmap_index(gy, gx, goc);
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
