//! HITS (Hyperlink-Induced Topic Search) hub/authority scoring on a directed graph.
//!
//! Exposed via C ABI for Python ctypes consumption.
//! Input:  Two CSR adjacency lists (forward and reverse) for the directed coupling graph.
//! Output: hub[n] and authority[n] arrays, L2-normalized.

const std = @import("std");

/// Compute HITS hub and authority scores.
///
/// Parameters:
///   n:             number of nodes
///   fwd_offsets:   CSR row offsets for forward edges, length n+1
///   fwd_targets:   CSR column indices for forward edges
///   rev_offsets:   CSR row offsets for reverse edges, length n+1
///   rev_targets:   CSR column indices for reverse edges
///   out_hub:       output hub scores, length n (caller-allocated)
///   out_auth:      output authority scores, length n (caller-allocated)
///   iterations:    number of power iterations
export fn smg_hits(
    n: u32,
    fwd_offsets: [*]const u32,
    fwd_targets: [*]const u32,
    rev_offsets: [*]const u32,
    rev_targets: [*]const u32,
    out_hub: [*]f64,
    out_auth: [*]f64,
    iterations: u32,
) callconv(.c) void {
    if (n == 0) return;

    // Initialize hub and authority to 1.0
    for (0..n) |i| {
        out_hub[i] = 1.0;
        out_auth[i] = 1.0;
    }

    for (0..iterations) |_| {
        // Authority update: auth(v) = sum of hub(u) for all u -> v
        // i.e., for each node v, iterate over its reverse neighbors (incoming edges)
        for (0..n) |vi| {
            var sum: f64 = 0.0;
            const start = rev_offsets[vi];
            const end = rev_offsets[vi + 1];
            for (start..end) |ei| {
                sum += out_hub[rev_targets[ei]];
            }
            out_auth[vi] = sum;
        }

        // Hub update: hub(v) = sum of auth(u) for all v -> u
        // i.e., for each node v, iterate over its forward neighbors (outgoing edges)
        for (0..n) |vi| {
            var sum: f64 = 0.0;
            const start = fwd_offsets[vi];
            const end = fwd_offsets[vi + 1];
            for (start..end) |ei| {
                sum += out_auth[fwd_targets[ei]];
            }
            out_hub[vi] = sum;
        }

        // L2 normalize authority
        var auth_sq_sum: f64 = 0.0;
        for (0..n) |i| {
            auth_sq_sum += out_auth[i] * out_auth[i];
        }
        const auth_norm = @max(@sqrt(auth_sq_sum), 1e-10);
        for (0..n) |i| {
            out_auth[i] /= auth_norm;
        }

        // L2 normalize hub
        var hub_sq_sum: f64 = 0.0;
        for (0..n) |i| {
            hub_sq_sum += out_hub[i] * out_hub[i];
        }
        const hub_norm = @max(@sqrt(hub_sq_sum), 1e-10);
        for (0..n) |i| {
            out_hub[i] /= hub_norm;
        }
    }
}
