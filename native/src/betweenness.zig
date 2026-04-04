//! Brandes' betweenness centrality on an undirected graph in CSR format.
//!
//! Exposed via C ABI for Python ctypes consumption.
//! Input:  CSR adjacency (offsets[n+1], targets[m]) where m = 2*|E| (undirected)
//! Output: bc[n] normalized betweenness values in [0, 1]

const std = @import("std");

/// Compute betweenness centrality using Brandes' algorithm.
///
/// Parameters:
///   n:          number of nodes
///   offsets:    CSR row offsets, length n+1. offsets[i]..offsets[i+1] = neighbors of node i
///   targets:    CSR column indices, length offsets[n]
///   out_bc:     output array, length n. Caller-allocated.
///   max_sources: if > 0 and < n, sample this many source nodes (approximate mode).
///                if 0, use all nodes (exact mode).
///   seed:       random seed for sampling
export fn smg_betweenness(
    n: u32,
    offsets: [*]const u32,
    targets: [*]const u32,
    out_bc: [*]f64,
    max_sources: u32,
    seed: u64,
) callconv(.c) void {
    if (n < 3) {
        for (0..n) |i| {
            out_bc[i] = 0.0;
        }
        return;
    }

    const allocator = std.heap.c_allocator;

    // Allocate working buffers
    const sigma = allocator.alloc(f64, n) catch return;
    defer allocator.free(sigma);
    const dist = allocator.alloc(i32, n) catch return;
    defer allocator.free(dist);
    const delta = allocator.alloc(f64, n) catch return;
    defer allocator.free(delta);
    // Stack: at most n entries
    const stack = allocator.alloc(u32, n) catch return;
    defer allocator.free(stack);
    // BFS queue
    const queue = allocator.alloc(u32, n) catch return;
    defer allocator.free(queue);
    // Predecessor lists: flat array of predecessor indices, offsets per node
    // Max predecessors = total edges, but we cap at m
    const m = offsets[n];
    const pred_buf = allocator.alloc(u32, m) catch return;
    defer allocator.free(pred_buf);
    const pred_offset = allocator.alloc(u32, n + 1) catch return;
    defer allocator.free(pred_offset);

    // Zero output
    for (0..n) |i| {
        out_bc[i] = 0.0;
    }

    // Determine source nodes
    const actual_sources = if (max_sources > 0 and max_sources < n) max_sources else n;
    const scale: f64 = @as(f64, @floatFromInt(n)) / @as(f64, @floatFromInt(actual_sources));

    // Source permutation (Fisher-Yates shuffle for sampling)
    const perm = allocator.alloc(u32, n) catch return;
    defer allocator.free(perm);
    for (0..n) |i| {
        perm[i] = @intCast(i);
    }
    if (actual_sources < n) {
        var rng = std.Random.DefaultPrng.init(seed);
        const random = rng.random();
        for (0..actual_sources) |i| {
            const j = i + random.intRangeLessThan(usize, 0, n - i);
            const tmp = perm[i];
            perm[i] = perm[j];
            perm[j] = tmp;
        }
    }

    // Brandes' algorithm
    for (0..actual_sources) |src_idx| {
        const s = perm[src_idx];

        // Initialize
        for (0..n) |i| {
            sigma[i] = 0.0;
            dist[i] = -1;
            delta[i] = 0.0;
            pred_offset[i] = 0;
        }
        pred_offset[n] = 0;

        sigma[s] = 1.0;
        dist[s] = 0;

        var stack_top: u32 = 0;
        var q_head: u32 = 0;
        var q_tail: u32 = 0;
        queue[q_tail] = s;
        q_tail += 1;

        // Track predecessor counts first pass
        var total_preds: u32 = 0;

        // BFS
        while (q_head < q_tail) {
            const v = queue[q_head];
            q_head += 1;
            stack[stack_top] = v;
            stack_top += 1;

            const next_dist = dist[v] + 1;
            const start = offsets[v];
            const end = offsets[v + 1];
            for (start..end) |ei| {
                const w = targets[ei];
                if (dist[w] < 0) {
                    dist[w] = next_dist;
                    queue[q_tail] = w;
                    q_tail += 1;
                }
                if (dist[w] == next_dist) {
                    sigma[w] += sigma[v];
                    // Record predecessor
                    if (total_preds < m) {
                        pred_buf[total_preds] = v;
                        total_preds += 1;
                        pred_offset[w + 1] = pred_offset[w + 1] + 1;
                    }
                }
            }
        }

        // Convert pred_offset counts to cumulative offsets (prefix sum)
        // But we need to rebuild: pred_offset[i] = start index for node i's predecessors
        // Currently pred_offset[i+1] holds the COUNT for node i
        // We need to repack. Instead, let's just use a second pass approach.
        // Actually the above approach is buggy for predecessor offsets. Let me use
        // a simpler flat scan: we know total_preds, let's just do a linear scan.

        // Simpler approach: rebuild predecessors from the stack
        // Reset pred tracking
        for (0..n + 1) |i| {
            pred_offset[i] = 0;
        }

        // Count predecessors per node
        {
            // Re-derive predecessors from dist and adjacency
            for (0..n) |vi| {
                const v: u32 = @intCast(vi);
                if (dist[v] < 0) continue;
                const vstart = offsets[v];
                const vend = offsets[v + 1];
                for (vstart..vend) |ei| {
                    const w = targets[ei];
                    if (dist[w] == dist[v] + 1) {
                        pred_offset[w] += 1;
                    }
                }
            }
        }

        // Prefix sum
        {
            var cumulative: u32 = 0;
            for (0..n) |i| {
                const count = pred_offset[i];
                pred_offset[i] = cumulative;
                cumulative += count;
            }
            pred_offset[n] = cumulative;
        }

        // Fill predecessor buffer
        const write_pos = allocator.alloc(u32, n) catch return;
        defer allocator.free(write_pos);
        for (0..n) |i| {
            write_pos[i] = pred_offset[i];
        }
        for (0..n) |vi| {
            const v: u32 = @intCast(vi);
            if (dist[v] < 0) continue;
            const vstart = offsets[v];
            const vend = offsets[v + 1];
            for (vstart..vend) |ei| {
                const w = targets[ei];
                if (dist[w] == dist[v] + 1) {
                    pred_buf[write_pos[w]] = v;
                    write_pos[w] += 1;
                }
            }
        }

        // Back-propagation (reverse order of stack)
        while (stack_top > 0) {
            stack_top -= 1;
            const w = stack[stack_top];
            const coeff = (1.0 + delta[w]) / sigma[w];
            const pstart = pred_offset[w];
            const pend = if (w + 1 < n) pred_offset[w + 1] else pred_offset[n];
            for (pstart..pend) |pi| {
                const v = pred_buf[pi];
                delta[v] += sigma[v] * coeff;
            }
            if (w != s) {
                out_bc[w] += delta[w];
            }
        }
    }

    // Normalize
    const norm: f64 = @as(f64, @floatFromInt(n - 1)) * @as(f64, @floatFromInt(n - 2));
    if (norm > 0.0) {
        const inv = scale / norm;
        for (0..n) |i| {
            out_bc[i] *= inv;
        }
    }
}
