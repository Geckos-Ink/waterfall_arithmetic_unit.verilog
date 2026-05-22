// WAU sample: a single Transformer encoder block.
//
//     a   = LayerNorm( x + MultiHeadSelfAttention(x) )
//     y   = LayerNorm( a + FFN(a) )
//
// where:
//     MultiHeadSelfAttention(x):
//         Q = x · Wq + bq      (split into N_HEADS · HEAD_DIM)
//         K = x · Wk + bk
//         V = x · Wv + bv
//         per head h:
//             S_h = softmax( Q_h · K_h^T / sqrt(HEAD_DIM) )       // [SEQ, SEQ]
//             A_h = S_h · V_h                                     // [SEQ, HEAD_DIM]
//         attn = concat_h(A_h) · Wo + bo
//
//     FFN(a) = relu(a · W1 + b1) · W2 + b2
//
// Shapes (row-major):
//     x, y, a               : [SEQ, D_MODEL]
//     Wq, Wk, Wv, Wo        : [D_MODEL, D_MODEL]
//     bq, bk, bv, bo        : [D_MODEL]
//     W1                    : [D_FF,    D_MODEL]
//     W2                    : [D_MODEL, D_FF]
//     b1                    : [D_FF]
//     b2                    : [D_MODEL]
//     ln1_gamma, ln1_beta   : [D_MODEL]
//     ln2_gamma, ln2_beta   : [D_MODEL]
//
// This sample demonstrates `alias` declarations — compile-time typedefs.
// Swapping `real` here retargets activations and weights of the entire
// block to a new precision in one place, while `accum` keeps matmul
// accumulation in a wider type. Future alias bodies will be able to
// declare compiler-side magic methods that govern how this type
// interoperates with other types — e.g. mixed-precision promotion
// between float16/float32, fused QKV/softmax lowering on cores that
// expose it, or vector/scalar layout hints.
// @wau lane_parallelism=4
// @wau max_in_flight=4
// @wau preferred_dtype=float32
// @wau placement_policy=balance
// @wau lowering_profile=throughput_optimized
// @wau program_priority=4
// @wau program_load_balance=least_busy

// --- Type aliases ----------------------------------------------------------
alias real      = float32;   // activation / weight storage
alias accum     = float32;   // matmul / reduction accumulator
alias attn_prob = float32;   // softmax-domain probability
alias index     = int32;

void main() {
    index SEQ      = 64;
    index D_MODEL  = 128;
    index N_HEADS  = 4;
    index HEAD_DIM = 32;     // must equal D_MODEL / N_HEADS
    index D_FF     = 256;

    DRAM real x[]   = system.bind_dram("tf_input");

    DRAM real Wq[]  = system.bind_dram("tf_Wq");
    DRAM real Wk[]  = system.bind_dram("tf_Wk");
    DRAM real Wv[]  = system.bind_dram("tf_Wv");
    DRAM real Wo[]  = system.bind_dram("tf_Wo");
    DRAM real bq[]  = system.bind_dram("tf_bq");
    DRAM real bk[]  = system.bind_dram("tf_bk");
    DRAM real bv[]  = system.bind_dram("tf_bv");
    DRAM real bo[]  = system.bind_dram("tf_bo");

    DRAM real W1[]  = system.bind_dram("tf_W1");
    DRAM real W2[]  = system.bind_dram("tf_W2");
    DRAM real b1[]  = system.bind_dram("tf_b1");
    DRAM real b2[]  = system.bind_dram("tf_b2");

    DRAM real ln1_gamma[] = system.bind_dram("tf_ln1_gamma");
    DRAM real ln1_beta[]  = system.bind_dram("tf_ln1_beta");
    DRAM real ln2_gamma[] = system.bind_dram("tf_ln2_gamma");
    DRAM real ln2_beta[]  = system.bind_dram("tf_ln2_beta");

    DRAM real y[]   = system.bind_dram("tf_output");

    transformer_encoder_block block = new transformer_encoder_block();
    block.configure(
        x,
        Wq, Wk, Wv, Wo, bq, bk, bv, bo,
        W1, W2, b1, b2,
        ln1_gamma, ln1_beta, ln2_gamma, ln2_beta,
        y,
        SEQ, D_MODEL, N_HEADS, HEAD_DIM, D_FF
    );
    block.run();
}

space transformer_encoder_block {
    // Tile / block sizes.
    index SEQ_BLOCK   = 16;
    index MODEL_BLOCK = 16;
    index FF_BLOCK    = 32;

    // DRAM-backed tensors.
    DRAM real x[];
    DRAM real Wq[]; DRAM real Wk[]; DRAM real Wv[]; DRAM real Wo[];
    DRAM real bq[]; DRAM real bk[]; DRAM real bv[]; DRAM real bo[];
    DRAM real W1[]; DRAM real W2[];
    DRAM real b1[]; DRAM real b2[];
    DRAM real ln1_gamma[]; DRAM real ln1_beta[];
    DRAM real ln2_gamma[]; DRAM real ln2_beta[];
    DRAM real y[];

    // Runtime dimensions.
    index SEQ;
    index D_MODEL;
    index N_HEADS;
    index HEAD_DIM;
    index D_FF;

    // On-chip working set. Sized for the example dims above; resize
    // proportionally for a larger model.
    real  x_buf       [64, 128];        // [SEQ, D_MODEL]
    real  q_buf       [64, 128];        // [SEQ, D_MODEL]
    real  k_buf       [64, 128];        // [SEQ, D_MODEL]
    real  v_buf       [64, 128];        // [SEQ, D_MODEL]
    accum attn_concat [64, 128];        // [SEQ, D_MODEL]
    real  attn_out    [64, 128];        // [SEQ, D_MODEL]  (after Wo and residual)
    real  ln1_out     [64, 128];        // [SEQ, D_MODEL]  (LN1 result)
    accum ff_mid      [64, 256];        // [SEQ, D_FF]
    real  ff_out      [64, 128];        // [SEQ, D_MODEL]

    // Per-head scratch.
    accum     scores [64, 64];          // [SEQ, SEQ]  (re-used per head)
    attn_prob probs  [64, 64];          // softmax-normalized scores

    // Bias / LN scratch.
    real  bias_block [32];              // up to FF_BLOCK
    real  ln_gamma   [128];
    real  ln_beta    [128];

    // Shared execution state.
    index current_head;
    index current_proj;                 // 0=Wq, 1=Wk, 2=Wv
    index current_seq_base;
    index current_model_base;
    index current_ff_base;
    index current_inner_base;

    tile_loader  loader;
    block_writer writer;
    proj_worker  proj_workers [16];
    head_worker  head_workers [16];
    ffn_worker   ffn_workers  [16];

    void configure(
        DRAM real input_x[],
        DRAM real input_Wq[], DRAM real input_Wk[], DRAM real input_Wv[], DRAM real input_Wo[],
        DRAM real input_bq[], DRAM real input_bk[], DRAM real input_bv[], DRAM real input_bo[],
        DRAM real input_W1[], DRAM real input_W2[],
        DRAM real input_b1[], DRAM real input_b2[],
        DRAM real input_ln1_gamma[], DRAM real input_ln1_beta[],
        DRAM real input_ln2_gamma[], DRAM real input_ln2_beta[],
        DRAM real output_y[],
        index seq, index d_model, index n_heads, index head_dim, index d_ff
    ) {
        x  = input_x;
        Wq = input_Wq; Wk = input_Wk; Wv = input_Wv; Wo = input_Wo;
        bq = input_bq; bk = input_bk; bv = input_bv; bo = input_bo;
        W1 = input_W1; W2 = input_W2;
        b1 = input_b1; b2 = input_b2;
        ln1_gamma = input_ln1_gamma; ln1_beta = input_ln1_beta;
        ln2_gamma = input_ln2_gamma; ln2_beta = input_ln2_beta;
        y  = output_y;

        SEQ      = seq;
        D_MODEL  = d_model;
        N_HEADS  = n_heads;
        HEAD_DIM = head_dim;
        D_FF     = d_ff;

        init();
    }

    void init() {
        for (index lane = 0; lane < proj_workers.count; lane++) {
            proj_workers[lane].set_lane(lane);
        }
        for (index lane = 0; lane < head_workers.count; lane++) {
            head_workers[lane].set_lane(lane);
        }
        for (index lane = 0; lane < ffn_workers.count; lane++) {
            ffn_workers[lane].set_lane(lane);
        }
    }

    void run() {
        loader.load_input_into_x_buf();

        // --- Multi-head self-attention ----------------------------------

        // QKV projections. Three separate matmuls of shape
        // [SEQ, D_MODEL] x [D_MODEL, D_MODEL] -> [SEQ, D_MODEL].
        current_proj = 0; project_input(Wq, bq, q_buf);
        current_proj = 1; project_input(Wk, bk, k_buf);
        current_proj = 2; project_input(Wv, bv, v_buf);

        // Per head: scores -> softmax -> · V_h.
        for (index h = 0; h < N_HEADS; h++) {
            current_head = h;

            for (index lane = 0; lane < head_workers.count; lane++) {
                head_workers[lane].compute_scores();
            }
            for (index lane = 0; lane < head_workers.count; lane++) {
                head_workers[lane].softmax_rows();
            }
            for (index lane = 0; lane < head_workers.count; lane++) {
                head_workers[lane].apply_to_values();
            }
        }

        // Output projection + residual: attn_out = attn_concat · Wo + bo + x.
        finalize_attention_projection();

        // LayerNorm 1.
        loader.load_ln_params(ln1_gamma, ln1_beta);
        for (index lane = 0; lane < ffn_workers.count; lane++) {
            ffn_workers[lane].layernorm_attn_out_to_ln1_out();
        }

        // --- Feed-forward network ---------------------------------------

        // ff_mid = relu(ln1_out · W1 + b1)  ; shape [SEQ, D_FF].
        compute_ffn_first_layer();

        // ff_out = ff_mid · W2 + b2 + ln1_out (residual).
        finalize_ffn_second_layer();

        // LayerNorm 2 over (ln1_out + ff_out)  -> y (writeback).
        loader.load_ln_params(ln2_gamma, ln2_beta);
        for (index lane = 0; lane < ffn_workers.count; lane++) {
            ffn_workers[lane].layernorm_ffn_out_to_y_buf();
        }

        writer.store_output();
    }

    // QKV projection driver: out_buf[s, d] = sum_k x_buf[s, k] * Wproj[d, k] + b[d].
    void project_input(DRAM real Wproj[], DRAM real bias_src[], real out_buf[][]) {
        for (index dm = 0; dm < D_MODEL; dm += MODEL_BLOCK) {
            current_model_base = dm;
            loader.load_bias_block_model(bias_src, current_model_base);

            for (index lane = 0; lane < proj_workers.count; lane++) {
                proj_workers[lane].project_block(Wproj, out_buf);
            }
        }
    }

    void finalize_attention_projection() {
        for (index dm = 0; dm < D_MODEL; dm += MODEL_BLOCK) {
            current_model_base = dm;
            loader.load_bias_block_model(bo, current_model_base);

            for (index lane = 0; lane < proj_workers.count; lane++) {
                proj_workers[lane].project_attn_out(Wo);
            }
        }
    }

    void compute_ffn_first_layer() {
        for (index ff = 0; ff < D_FF; ff += FF_BLOCK) {
            current_ff_base = ff;
            loader.load_bias_block_ff(b1, current_ff_base);

            for (index lane = 0; lane < ffn_workers.count; lane++) {
                ffn_workers[lane].ffn_first_block(W1);
            }
        }
    }

    void finalize_ffn_second_layer() {
        for (index dm = 0; dm < D_MODEL; dm += MODEL_BLOCK) {
            current_model_base = dm;
            loader.load_bias_block_model(b2, current_model_base);

            for (index lane = 0; lane < ffn_workers.count; lane++) {
                ffn_workers[lane].ffn_second_block(W2);
            }
        }
    }

    index x_index(index s, index d)      { return s * D_MODEL + d; }
    index y_index(index s, index d)      { return s * D_MODEL + d; }
    index w_model_index(index d_out, index d_in) {
        return d_out * D_MODEL + d_in;
    }
    index w1_index(index ff, index d_in) {
        return ff * D_MODEL + d_in;
    }
    index w2_index(index d_out, index ff) {
        return d_out * D_FF + ff;
    }

    space tile_loader {
        void load_input_into_x_buf() {
            for (index s = 0; s < SEQ; s++) {
                for (index d = 0; d < D_MODEL; d++) {
                    x_buf[s, d] = x[x_index(s, d)];
                }
            }
        }

        void load_bias_block_model(DRAM real bias_src[], index base) {
            for (index oc = 0; oc < MODEL_BLOCK; oc++) {
                index g = base + oc;
                if (g < D_MODEL) {
                    bias_block[oc] = bias_src[g];
                } else {
                    bias_block[oc] = 0.0f;
                }
            }
        }

        void load_bias_block_ff(DRAM real bias_src[], index base) {
            for (index oc = 0; oc < FF_BLOCK; oc++) {
                index g = base + oc;
                if (g < D_FF) {
                    bias_block[oc] = bias_src[g];
                } else {
                    bias_block[oc] = 0.0f;
                }
            }
        }

        void load_ln_params(DRAM real gamma[], DRAM real beta[]) {
            for (index d = 0; d < D_MODEL; d++) {
                ln_gamma[d] = gamma[d];
                ln_beta[d]  = beta[d];
            }
        }
    }

    space block_writer {
        void store_output() {
            // y already lives in y_buf-equivalent (we wrote into x_buf's
            // companion via the LN pass); flush it to DRAM.
            for (index s = 0; s < SEQ; s++) {
                for (index d = 0; d < D_MODEL; d++) {
                    y[y_index(s, d)] = ln1_out[s, d];   // see ln pass note below
                }
            }
        }
    }

    // Worker for the three QKV projections and the final attn output proj.
    space proj_worker {
        index lane;

        void set_lane(index lane_id) {
            lane = lane_id;
        }

        // out_buf[s, current_model_base + lane] += sum_k x_buf[s, k] * Wproj[d_out, k] + bias
        void project_block(DRAM real Wproj[], real out_buf[][]) {
            index d_out = current_model_base + lane;
            if (d_out >= D_MODEL) {
                return;
            }
            for (index s = 0; s < SEQ; s++) {
                accum sum = bias_block[lane];
                for (index k = 0; k < D_MODEL; k++) {
                    real xv = x_buf[s, k];
                    real wv = Wproj[w_model_index(d_out, k)];
                    sum = sum + (xv * wv);
                }
                out_buf[s, d_out] = sum;
            }
        }

        // attn_out[s, d_out] = bo[d_out] + x_buf[s, d_out] + sum_k attn_concat[s, k] * Wo[d_out, k]
        void project_attn_out(DRAM real Wo_w[]) {
            index d_out = current_model_base + lane;
            if (d_out >= D_MODEL) {
                return;
            }
            for (index s = 0; s < SEQ; s++) {
                accum sum = bias_block[lane];
                sum = sum + x_buf[s, d_out];      // residual
                for (index k = 0; k < D_MODEL; k++) {
                    real av = attn_concat[s, k];
                    real wv = Wo_w[w_model_index(d_out, k)];
                    sum = sum + (av * wv);
                }
                attn_out[s, d_out] = sum;
            }
        }
    }

    // Worker for the per-head attention math. Each lane owns a row-stride
    // through SEQ so workers cooperate across the whole [SEQ, SEQ] score
    // matrix without contention.
    space head_worker {
        index lane;

        void set_lane(index lane_id) {
            lane = lane_id;
        }

        // scores[s, t] = sum_k Q_h[s, k] * K_h[t, k] / sqrt(HEAD_DIM)
        void compute_scores() {
            real inv_scale = rsqrt((real) HEAD_DIM);
            index head_base = current_head * HEAD_DIM;
            for (index s = lane; s < SEQ; s += head_workers.count) {
                for (index t = 0; t < SEQ; t++) {
                    accum dot = 0.0f;
                    for (index k = 0; k < HEAD_DIM; k++) {
                        real qv = q_buf[s, head_base + k];
                        real kv = k_buf[t, head_base + k];
                        dot = dot + (qv * kv);
                    }
                    scores[s, t] = dot * inv_scale;
                }
            }
        }

        // probs[s, *] = softmax_along_t( scores[s, *] )
        void softmax_rows() {
            for (index s = lane; s < SEQ; s += head_workers.count) {
                accum row_max = scores[s, 0];
                for (index t = 1; t < SEQ; t++) {
                    accum v = scores[s, t];
                    if (v > row_max) {
                        row_max = v;
                    }
                }

                accum row_sum = 0.0f;
                for (index t = 0; t < SEQ; t++) {
                    attn_prob e = exp(scores[s, t] - row_max);
                    probs[s, t] = e;
                    row_sum = row_sum + e;
                }

                accum inv_sum = 1.0f / row_sum;
                for (index t = 0; t < SEQ; t++) {
                    probs[s, t] = probs[s, t] * inv_sum;
                }
            }
        }

        // attn_concat[s, head_base + d] = sum_t probs[s, t] * V_h[t, d]
        void apply_to_values() {
            index head_base = current_head * HEAD_DIM;
            for (index s = lane; s < SEQ; s += head_workers.count) {
                for (index d = 0; d < HEAD_DIM; d++) {
                    accum sum = 0.0f;
                    for (index t = 0; t < SEQ; t++) {
                        attn_prob p = probs[s, t];
                        real vv = v_buf[t, head_base + d];
                        sum = sum + (p * vv);
                    }
                    attn_concat[s, head_base + d] = sum;
                }
            }
        }
    }

    // Worker for the feed-forward sub-block and both LayerNorms.
    space ffn_worker {
        index lane;

        void set_lane(index lane_id) {
            lane = lane_id;
        }

        // LayerNorm over D_MODEL for each sequence position;
        // result of (x + attn) lives in attn_out, written into ln1_out.
        void layernorm_attn_out_to_ln1_out() {
            for (index s = lane; s < SEQ; s += ffn_workers.count) {
                accum mean = 0.0f;
                for (index d = 0; d < D_MODEL; d++) {
                    mean = mean + attn_out[s, d];
                }
                mean = mean / (accum) D_MODEL;

                accum var = 0.0f;
                for (index d = 0; d < D_MODEL; d++) {
                    accum diff = attn_out[s, d] - mean;
                    var = var + (diff * diff);
                }
                var = var / (accum) D_MODEL;

                accum inv_std = rsqrt(var + 1.0e-5f);
                for (index d = 0; d < D_MODEL; d++) {
                    accum normed = (attn_out[s, d] - mean) * inv_std;
                    ln1_out[s, d] = (normed * ln_gamma[d]) + ln_beta[d];
                }
            }
        }

        // ff_mid[s, current_ff_base + lane] = relu( bias + sum_k ln1_out[s, k] * W1[d_out, k] )
        void ffn_first_block(DRAM real W1_w[]) {
            index d_out = current_ff_base + lane;
            if (d_out >= D_FF) {
                return;
            }
            for (index s = 0; s < SEQ; s++) {
                accum sum = bias_block[lane];
                for (index k = 0; k < D_MODEL; k++) {
                    real av = ln1_out[s, k];
                    real wv = W1_w[w1_index(d_out, k)];
                    sum = sum + (av * wv);
                }
                if (sum < 0.0f) {
                    sum = 0.0f;
                }
                ff_mid[s, d_out] = sum;
            }
        }

        // ff_out[s, current_model_base + lane] = bias + sum_k ff_mid[s, k] * W2[d_out, k]
        void ffn_second_block(DRAM real W2_w[]) {
            index d_out = current_model_base + lane;
            if (d_out >= D_MODEL) {
                return;
            }
            for (index s = 0; s < SEQ; s++) {
                accum sum = bias_block[lane];
                for (index k = 0; k < D_FF; k++) {
                    real av = ff_mid[s, k];
                    real wv = W2_w[w2_index(d_out, k)];
                    sum = sum + (av * wv);
                }
                ff_out[s, d_out] = sum;
            }
        }

        // y_pos = LayerNorm( ln1_out + ff_out ).  Result is parked back in
        // ln1_out so block_writer flushes it to DRAM with a single pass.
        void layernorm_ffn_out_to_y_buf() {
            for (index s = lane; s < SEQ; s += ffn_workers.count) {
                accum mean = 0.0f;
                for (index d = 0; d < D_MODEL; d++) {
                    mean = mean + (ln1_out[s, d] + ff_out[s, d]);
                }
                mean = mean / (accum) D_MODEL;

                accum var = 0.0f;
                for (index d = 0; d < D_MODEL; d++) {
                    accum sum = ln1_out[s, d] + ff_out[s, d];
                    accum diff = sum - mean;
                    var = var + (diff * diff);
                }
                var = var / (accum) D_MODEL;

                accum inv_std = rsqrt(var + 1.0e-5f);
                for (index d = 0; d < D_MODEL; d++) {
                    accum sum = ln1_out[s, d] + ff_out[s, d];
                    accum normed = (sum - mean) * inv_std;
                    ln1_out[s, d] = (normed * ln_gamma[d]) + ln_beta[d];
                }
            }
        }
    }
}
