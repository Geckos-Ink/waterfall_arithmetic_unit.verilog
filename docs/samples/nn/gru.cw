// WAU sample: GRU (Gated Recurrent Unit) cell rolled over a sequence.
// For each time step t in [0, SEQ):
//     z = sigmoid( x_t · Wz + h · Uz + bz )            // update gate
//     r = sigmoid( x_t · Wr + h · Ur + br )            // reset gate
//     n = tanh   ( x_t · Wh + (r * h) · Uh + bh )      // candidate state
//     h = (1 - z) * n + z * h                          // new hidden state
//
// Shapes (row-major):
//     x[SEQ, BATCH, IN]
//     h[BATCH, HID]            ping/pong on chip
//     Wz, Wr, Wh : [HID, IN]
//     Uz, Ur, Uh : [HID, HID]
//     bz, br, bh : [HID]
//     y[SEQ, BATCH, HID]       (full sequence of hidden states)
//
// This sample uses the `alias` syntax — a compile-time typedef. Changing
// `real` here swaps the working precision of the whole cell, while
// `accum` keeps matmul accumulation in a wider type. Future alias bodies
// will be able to declare compiler-side magic methods controlling how
// this type interoperates with other types (mixed-precision promotion,
// fused sigmoid/tanh lowering, scalar/vector hints, etc.).
// @wau lane_parallelism=4
// @wau max_in_flight=4
// @wau preferred_dtype=float32
// @wau placement_policy=balance
// @wau lowering_profile=throughput_optimized
// @wau program_priority=4
// @wau program_load_balance=least_busy

// --- Type aliases ----------------------------------------------------------
alias real    = float32;   // weight / activation storage
alias accum   = float32;   // matmul accumulator
alias gate    = float32;   // gate / nonlinearity domain
alias index   = int32;

void main() {
    index SEQ   = 32;
    index BATCH = 8;
    index IN    = 128;
    index HID   = 128;

    DRAM real x[]  = system.bind_dram("gru_input_sequence");

    DRAM real Wz[] = system.bind_dram("gru_Wz");
    DRAM real Wr[] = system.bind_dram("gru_Wr");
    DRAM real Wh[] = system.bind_dram("gru_Wh");

    DRAM real Uz[] = system.bind_dram("gru_Uz");
    DRAM real Ur[] = system.bind_dram("gru_Ur");
    DRAM real Uh[] = system.bind_dram("gru_Uh");

    DRAM real bz[] = system.bind_dram("gru_bz");
    DRAM real br[] = system.bind_dram("gru_br");
    DRAM real bh[] = system.bind_dram("gru_bh");

    DRAM real h0[] = system.bind_dram("gru_h0");
    DRAM real y[]  = system.bind_dram("gru_hidden_sequence");

    gru_cell cell = new gru_cell();
    cell.configure(
        x,
        Wz, Wr, Wh,
        Uz, Ur, Uh,
        bz, br, bh,
        h0, y,
        SEQ, BATCH, IN, HID
    );
    cell.run();
}

space gru_cell {
    // Tile / block sizes. Picked small so the working set fits comfortably
    // on-chip on the smaller device presets; tunable per device.
    index HID_BLOCK = 16;
    index IN_BLOCK  = 32;

    // DRAM-backed tensors.
    DRAM real x[];                      // [SEQ, BATCH, IN]
    DRAM real Wz[]; DRAM real Wr[]; DRAM real Wh[];   // [HID, IN]
    DRAM real Uz[]; DRAM real Ur[]; DRAM real Uh[];   // [HID, HID]
    DRAM real bz[]; DRAM real br[]; DRAM real bh[];   // [HID]
    DRAM real h0[];                     // [BATCH, HID]
    DRAM real y[];                      // [SEQ, BATCH, HID]

    // Runtime dimensions.
    index SEQ;
    index BATCH;
    index IN;
    index HID;

    // Hidden state ping/pong (read previous step, write new step).
    real  h_ping[8, 128];               // [BATCH, HID]
    real  h_pong[8, 128];
    // Modulated hidden state  r * h_prev, reused across timesteps.
    real  rh_buf[8, 128];

    // Per-timestep gate buffers (full HID).
    accum z_buf[8, 128];
    accum r_buf[8, 128];
    accum n_buf[8, 128];

    // Tile buffers for x_t, weights, bias.
    real  x_tile[8, 32];                // [BATCH, IN_BLOCK]
    real  w_block[16, 32];              // [HID_BLOCK, IN_BLOCK]
    real  u_block[16, 16];              // [HID_BLOCK, HID_BLOCK_K]
    real  h_tile[8, 16];                // [BATCH, HID_BLOCK_K]
    real  bias_block[16];               // [HID_BLOCK]

    // Shared execution state.
    index current_t;
    index current_hid_base;
    index current_in_base;
    index current_hk_base;
    index current_hidden_buffer;        // 0 -> h_ping is "previous"
    index current_gate;                 // 0=z, 1=r, 2=n

    gate_loader loader;
    seq_writer  writer;
    hid_worker  workers[16];

    void configure(
        DRAM real input_seq[],
        DRAM real input_Wz[], DRAM real input_Wr[], DRAM real input_Wh[],
        DRAM real input_Uz[], DRAM real input_Ur[], DRAM real input_Uh[],
        DRAM real input_bz[], DRAM real input_br[], DRAM real input_bh[],
        DRAM real input_h0[], DRAM real output_seq[],
        index seq, index batch, index in_features, index hidden
    ) {
        x  = input_seq;
        Wz = input_Wz; Wr = input_Wr; Wh = input_Wh;
        Uz = input_Uz; Ur = input_Ur; Uh = input_Uh;
        bz = input_bz; br = input_br; bh = input_bh;
        h0 = input_h0;
        y  = output_seq;

        SEQ   = seq;
        BATCH = batch;
        IN    = in_features;
        HID   = hidden;

        init();
    }

    void init() {
        for (index lane = 0; lane < workers.count; lane++) {
            workers[lane].set_lane(lane);
        }
        loader.load_initial_hidden();
        current_hidden_buffer = 0;
    }

    void run() {
        for (index t = 0; t < SEQ; t++) {
            current_t = t;

            // Stage 1: pre-activation for update gate z.
            current_gate = 0;
            compute_gate(Wz, Uz, bz, false);
            for (index lane = 0; lane < workers.count; lane++) {
                workers[lane].apply_sigmoid_to_z();
            }

            // Stage 2: pre-activation for reset gate r.
            current_gate = 1;
            compute_gate(Wr, Ur, br, false);
            for (index lane = 0; lane < workers.count; lane++) {
                workers[lane].apply_sigmoid_to_r();
            }

            // Stage 3: modulate previous hidden by r and compute candidate n.
            for (index lane = 0; lane < workers.count; lane++) {
                workers[lane].build_rh();
            }
            current_gate = 2;
            compute_gate(Wh, Uh, bh, true);
            for (index lane = 0; lane < workers.count; lane++) {
                workers[lane].apply_tanh_to_n();
            }

            // Stage 4: blend new hidden state and commit to ping/pong.
            for (index lane = 0; lane < workers.count; lane++) {
                workers[lane].commit_new_hidden();
            }

            writer.store_hidden_step(current_t);

            // Swap ping/pong for next timestep.
            current_hidden_buffer = 1 - current_hidden_buffer;
        }
    }

    // Drives matmul-then-add for one gate (Wgate · x_t + Ugate · h_or_rh + bias),
    // writing into the gate buffer selected by current_gate.
    void compute_gate(
        DRAM real Wgate[],
        DRAM real Ugate[],
        DRAM real bgate[],
        bool use_rh_for_hidden_term
    ) {
        for (index oc = 0; oc < HID; oc += HID_BLOCK) {
            current_hid_base = oc;

            loader.load_bias_block(bgate, current_hid_base);

            // Clear the slice of the gate accumulator covered by this block.
            for (index lane = 0; lane < workers.count; lane++) {
                workers[lane].clear_gate_accumulator();
            }

            // Input-projection contribution: Wgate · x_t.
            for (index cb = 0; cb < IN; cb += IN_BLOCK) {
                current_in_base = cb;
                loader.load_x_tile(current_t, current_in_base);
                loader.load_w_block(Wgate, current_hid_base, current_in_base);

                for (index lane = 0; lane < workers.count; lane++) {
                    workers[lane].accumulate_w_x();
                }
            }

            // Hidden-projection contribution: Ugate · (h_prev or r*h_prev).
            for (index kb = 0; kb < HID; kb += HID_BLOCK) {
                current_hk_base = kb;
                loader.load_h_tile(current_hk_base, use_rh_for_hidden_term);
                loader.load_u_block(Ugate, current_hid_base, current_hk_base);

                for (index lane = 0; lane < workers.count; lane++) {
                    workers[lane].accumulate_u_h();
                }
            }

            // Bias add (still pre-activation).
            for (index lane = 0; lane < workers.count; lane++) {
                workers[lane].add_bias();
            }
        }
    }

    index x_index(index t, index batch_i, index in_feat) {
        return ((t * BATCH) + batch_i) * IN + in_feat;
    }

    index y_index(index t, index batch_i, index hid_unit) {
        return ((t * BATCH) + batch_i) * HID + hid_unit;
    }

    index w_index(index hid_unit, index in_feat) {
        return hid_unit * IN + in_feat;
    }

    index u_index(index hid_unit, index hid_k) {
        return hid_unit * HID + hid_k;
    }

    index h0_index(index batch_i, index hid_unit) {
        return batch_i * HID + hid_unit;
    }

    space gate_loader {
        void load_initial_hidden() {
            for (index bi = 0; bi < BATCH; bi++) {
                for (index hu = 0; hu < HID; hu++) {
                    h_ping[bi, hu] = h0[h0_index(bi, hu)];
                    h_pong[bi, hu] = 0.0f;
                }
            }
        }

        void load_bias_block(DRAM real bias_src[], index hid_base) {
            for (index oc = 0; oc < HID_BLOCK; oc++) {
                index ghu = hid_base + oc;
                if (ghu < HID) {
                    bias_block[oc] = bias_src[ghu];
                } else {
                    bias_block[oc] = 0.0f;
                }
            }
        }

        void load_x_tile(index t, index in_base) {
            for (index bi = 0; bi < BATCH; bi++) {
                for (index ic = 0; ic < IN_BLOCK; ic++) {
                    index gic = in_base + ic;
                    real v = 0.0f;
                    if (gic < IN) {
                        v = x[x_index(t, bi, gic)];
                    }
                    x_tile[bi, ic] = v;
                }
            }
        }

        void load_w_block(DRAM real W[], index hid_base, index in_base) {
            for (index oc = 0; oc < HID_BLOCK; oc++) {
                for (index ic = 0; ic < IN_BLOCK; ic++) {
                    index ghu = hid_base + oc;
                    index gic = in_base + ic;
                    if (ghu < HID && gic < IN) {
                        w_block[oc, ic] = W[w_index(ghu, gic)];
                    } else {
                        w_block[oc, ic] = 0.0f;
                    }
                }
            }
        }

        // Loads h_prev or (r * h_prev) into h_tile depending on the flag.
        void load_h_tile(index hk_base, bool use_rh) {
            for (index bi = 0; bi < BATCH; bi++) {
                for (index hk = 0; hk < HID_BLOCK; hk++) {
                    index ghk = hk_base + hk;
                    real v = 0.0f;
                    if (ghk < HID) {
                        if (use_rh) {
                            v = rh_buf[bi, ghk];
                        } else {
                            if (current_hidden_buffer == 0) {
                                v = h_ping[bi, ghk];
                            } else {
                                v = h_pong[bi, ghk];
                            }
                        }
                    }
                    h_tile[bi, hk] = v;
                }
            }
        }

        void load_u_block(DRAM real U[], index hid_base, index hk_base) {
            for (index oc = 0; oc < HID_BLOCK; oc++) {
                for (index hk = 0; hk < HID_BLOCK; hk++) {
                    index ghu = hid_base + oc;
                    index ghk = hk_base + hk;
                    if (ghu < HID && ghk < HID) {
                        u_block[oc, hk] = U[u_index(ghu, ghk)];
                    } else {
                        u_block[oc, hk] = 0.0f;
                    }
                }
            }
        }
    }

    space seq_writer {
        void store_hidden_step(index t) {
            for (index bi = 0; bi < BATCH; bi++) {
                for (index hu = 0; hu < HID; hu++) {
                    real v = 0.0f;
                    // After commit, the freshly-written buffer is the
                    // *previous* one for the next iteration's perspective;
                    // here we want the just-written state, which lives in
                    // the OLD `current_hidden_buffer` since the run-loop
                    // swaps AFTER writing.
                    if (current_hidden_buffer == 0) {
                        v = h_pong[bi, hu];
                    } else {
                        v = h_ping[bi, hu];
                    }
                    y[y_index(t, bi, hu)] = v;
                }
            }
        }
    }

    space hid_worker {
        index lane;

        void set_lane(index lane_id) {
            lane = lane_id;
        }

        void clear_gate_accumulator() {
            index ghu = current_hid_base + lane;
            if (ghu >= HID) {
                return;
            }
            for (index bi = 0; bi < BATCH; bi++) {
                if (current_gate == 0) {
                    z_buf[bi, ghu] = 0.0f;
                } else if (current_gate == 1) {
                    r_buf[bi, ghu] = 0.0f;
                } else {
                    n_buf[bi, ghu] = 0.0f;
                }
            }
        }

        void accumulate_w_x() {
            index ghu = current_hid_base + lane;
            if (ghu >= HID) {
                return;
            }
            for (index bi = 0; bi < BATCH; bi++) {
                accum sum = read_gate(bi, ghu);
                for (index ic = 0; ic < IN_BLOCK; ic++) {
                    real xv = x_tile[bi, ic];
                    real wv = w_block[lane, ic];
                    sum = sum + (xv * wv);
                }
                write_gate(bi, ghu, sum);
            }
        }

        void accumulate_u_h() {
            index ghu = current_hid_base + lane;
            if (ghu >= HID) {
                return;
            }
            for (index bi = 0; bi < BATCH; bi++) {
                accum sum = read_gate(bi, ghu);
                for (index hk = 0; hk < HID_BLOCK; hk++) {
                    real hv = h_tile[bi, hk];
                    real uv = u_block[lane, hk];
                    sum = sum + (hv * uv);
                }
                write_gate(bi, ghu, sum);
            }
        }

        void add_bias() {
            index ghu = current_hid_base + lane;
            if (ghu >= HID) {
                return;
            }
            real bv = bias_block[lane];
            for (index bi = 0; bi < BATCH; bi++) {
                accum sum = read_gate(bi, ghu);
                sum = sum + bv;
                write_gate(bi, ghu, sum);
            }
        }

        void apply_sigmoid_to_z() {
            for (index hu = lane; hu < HID; hu += workers.count) {
                for (index bi = 0; bi < BATCH; bi++) {
                    gate v = z_buf[bi, hu];
                    z_buf[bi, hu] = sigmoid(v);
                }
            }
        }

        void apply_sigmoid_to_r() {
            for (index hu = lane; hu < HID; hu += workers.count) {
                for (index bi = 0; bi < BATCH; bi++) {
                    gate v = r_buf[bi, hu];
                    r_buf[bi, hu] = sigmoid(v);
                }
            }
        }

        void apply_tanh_to_n() {
            for (index hu = lane; hu < HID; hu += workers.count) {
                for (index bi = 0; bi < BATCH; bi++) {
                    gate v = n_buf[bi, hu];
                    n_buf[bi, hu] = tanh(v);
                }
            }
        }

        // rh = r * h_prev. Cached into rh_buf so the candidate-state
        // matmul can read it like any other hidden tile.
        void build_rh() {
            for (index hu = lane; hu < HID; hu += workers.count) {
                for (index bi = 0; bi < BATCH; bi++) {
                    real hp = 0.0f;
                    if (current_hidden_buffer == 0) {
                        hp = h_ping[bi, hu];
                    } else {
                        hp = h_pong[bi, hu];
                    }
                    real rv = r_buf[bi, hu];
                    rh_buf[bi, hu] = rv * hp;
                }
            }
        }

        // h_new = (1 - z) * n + z * h_prev, written into the inactive buffer.
        void commit_new_hidden() {
            for (index hu = lane; hu < HID; hu += workers.count) {
                for (index bi = 0; bi < BATCH; bi++) {
                    real hp = 0.0f;
                    if (current_hidden_buffer == 0) {
                        hp = h_ping[bi, hu];
                    } else {
                        hp = h_pong[bi, hu];
                    }
                    real zv = z_buf[bi, hu];
                    real nv = n_buf[bi, hu];
                    real hn = ((1.0f - zv) * nv) + (zv * hp);

                    if (current_hidden_buffer == 0) {
                        h_pong[bi, hu] = hn;
                    } else {
                        h_ping[bi, hu] = hn;
                    }
                }
            }
        }

        accum read_gate(index bi, index ghu) {
            if (current_gate == 0) {
                return z_buf[bi, ghu];
            } else if (current_gate == 1) {
                return r_buf[bi, ghu];
            } else {
                return n_buf[bi, ghu];
            }
        }

        void write_gate(index bi, index ghu, accum v) {
            if (current_gate == 0) {
                z_buf[bi, ghu] = v;
            } else if (current_gate == 1) {
                r_buf[bi, ghu] = v;
            } else {
                n_buf[bi, ghu] = v;
            }
        }
    }
}
