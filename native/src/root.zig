//! smg native accelerator — graph algorithms and extraction via C ABI.

comptime {
    _ = @import("betweenness.zig");
    _ = @import("hits.zig");
    _ = @import("extract.zig");
}
