const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});

    const lib = b.addLibrary(.{
        .name = "smg_accel",
        .root_module = b.createModule(.{
            .root_source_file = b.path("src/root.zig"),
            .target = target,
            .optimize = optimize,
            .link_libc = true,
        }),
        .linkage = .dynamic,
    });

    // tree-sitter core: compile lib.c (aggregator that includes all src files)
    lib.addCSourceFile(.{
        .file = b.path("vendor/tree-sitter/lib/src/lib.c"),
        .flags = &.{ "-std=gnu11", "-D_DEFAULT_SOURCE", "-fvisibility=hidden" },
    });
    lib.addIncludePath(b.path("vendor/tree-sitter/lib/include"));
    lib.addIncludePath(b.path("vendor/tree-sitter/lib/src"));

    // tree-sitter-python grammar
    lib.addCSourceFile(.{
        .file = b.path("vendor/tree-sitter-python/src/parser.c"),
        .flags = &.{"-std=c11"},
    });
    lib.addCSourceFile(.{
        .file = b.path("vendor/tree-sitter-python/src/scanner.c"),
        .flags = &.{"-std=c11"},
    });
    lib.addIncludePath(b.path("vendor/tree-sitter-python/src"));

    // Make tree-sitter headers available to Zig @cImport
    lib.root_module.addIncludePath(b.path("vendor/tree-sitter/lib/include"));

    b.installArtifact(lib);
}
