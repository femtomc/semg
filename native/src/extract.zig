//! Native entity extraction from tree-sitter ASTs.
//!
//! Parses source with tree-sitter, walks the AST to discover entities
//! (functions, classes, methods, constants), computes metrics and
//! structure hashes, and returns results as JSONL via a caller-provided buffer.
//!
//! Optimizations:
//!   - Tree-sitter cursor API for zero-copy AST traversal
//!   - Pointer comparison for node type strings (tree-sitter interns them)
//!   - Batch interface for multi-file extraction with threading

const std = @import("std");
const ts = @cImport({
    @cInclude("tree_sitter/api.h");
});

extern fn tree_sitter_python() callconv(.c) *ts.TSLanguage;

// --- Interned type pointers (resolved on first use) ---

var _type_ptrs_init: bool = false;

var _function_definition: [*c]const u8 = undefined;
var _class_definition: [*c]const u8 = undefined;
var _decorated_definition: [*c]const u8 = undefined;
var _expression_statement: [*c]const u8 = undefined;
var _assignment: [*c]const u8 = undefined;
var _identifier: [*c]const u8 = undefined;
var _attribute: [*c]const u8 = undefined;
var _call: [*c]const u8 = undefined;
var _import_statement: [*c]const u8 = undefined;
var _import_from_statement: [*c]const u8 = undefined;
var _dotted_name: [*c]const u8 = undefined;
var _comment: [*c]const u8 = undefined;
var _string: [*c]const u8 = undefined;
var _string_content: [*c]const u8 = undefined;
var _integer: [*c]const u8 = undefined;
var _float: [*c]const u8 = undefined;
var _none: [*c]const u8 = undefined;
var _true: [*c]const u8 = undefined;
var _false: [*c]const u8 = undefined;
var _return_statement: [*c]const u8 = undefined;
var _if_statement: [*c]const u8 = undefined;
var _elif_clause: [*c]const u8 = undefined;
var _for_statement: [*c]const u8 = undefined;
var _while_statement: [*c]const u8 = undefined;
var _except_clause: [*c]const u8 = undefined;
var _with_statement: [*c]const u8 = undefined;
var _conditional_expression: [*c]const u8 = undefined;
var _match_statement: [*c]const u8 = undefined;
var _case_clause: [*c]const u8 = undefined;
var _boolean_operator: [*c]const u8 = undefined;
var _try_statement: [*c]const u8 = undefined;

fn initTypePtrs(lang: *ts.TSLanguage) void {
    if (_type_ptrs_init) return;
    // Resolve type IDs and get their string pointers
    const n = ts.ts_language_symbol_count(lang);
    for (0..n) |i| {
        const name = ts.ts_language_symbol_name(lang, @intCast(i));
        if (name == null) continue;
        if (ptrEql(name, "function_definition")) _function_definition = name;
        if (ptrEql(name, "class_definition")) _class_definition = name;
        if (ptrEql(name, "decorated_definition")) _decorated_definition = name;
        if (ptrEql(name, "expression_statement")) _expression_statement = name;
        if (ptrEql(name, "assignment")) _assignment = name;
        if (ptrEql(name, "identifier")) _identifier = name;
        if (ptrEql(name, "attribute")) _attribute = name;
        if (ptrEql(name, "call")) _call = name;
        if (ptrEql(name, "import_statement")) _import_statement = name;
        if (ptrEql(name, "import_from_statement")) _import_from_statement = name;
        if (ptrEql(name, "dotted_name")) _dotted_name = name;
        if (ptrEql(name, "comment")) _comment = name;
        if (ptrEql(name, "string")) _string = name;
        if (ptrEql(name, "string_content")) _string_content = name;
        if (ptrEql(name, "integer")) _integer = name;
        if (ptrEql(name, "float")) _float = name;
        if (ptrEql(name, "none")) _none = name;
        if (ptrEql(name, "true")) _true = name;
        if (ptrEql(name, "false")) _false = name;
        if (ptrEql(name, "return_statement")) _return_statement = name;
        if (ptrEql(name, "if_statement")) _if_statement = name;
        if (ptrEql(name, "elif_clause")) _elif_clause = name;
        if (ptrEql(name, "for_statement")) _for_statement = name;
        if (ptrEql(name, "while_statement")) _while_statement = name;
        if (ptrEql(name, "except_clause")) _except_clause = name;
        if (ptrEql(name, "with_statement")) _with_statement = name;
        if (ptrEql(name, "conditional_expression")) _conditional_expression = name;
        if (ptrEql(name, "match_statement")) _match_statement = name;
        if (ptrEql(name, "case_clause")) _case_clause = name;
        if (ptrEql(name, "boolean_operator")) _boolean_operator = name;
        if (ptrEql(name, "try_statement")) _try_statement = name;
    }
    _type_ptrs_init = true;
}

fn ptrEql(ptr: [*c]const u8, comptime expected: []const u8) bool {
    return std.mem.eql(u8, std.mem.span(ptr), expected);
}

// --- Fast type checks via pointer comparison ---

inline fn isFunctionDef(t: [*c]const u8) bool {
    return t == _function_definition;
}
inline fn isClassDef(t: [*c]const u8) bool {
    return t == _class_definition;
}
inline fn isDecoratedDef(t: [*c]const u8) bool {
    return t == _decorated_definition;
}
inline fn isExprStmt(t: [*c]const u8) bool {
    return t == _expression_statement;
}
inline fn isAssignment(t: [*c]const u8) bool {
    return t == _assignment;
}
inline fn isIdentifier(t: [*c]const u8) bool {
    return t == _identifier;
}
inline fn isAttribute(t: [*c]const u8) bool {
    return t == _attribute;
}
inline fn isCall(t: [*c]const u8) bool {
    return t == _call;
}
inline fn isComment(t: [*c]const u8) bool {
    return t == _comment;
}

inline fn isBranch(t: [*c]const u8) bool {
    return t == _if_statement or t == _elif_clause or t == _for_statement or
        t == _while_statement or t == _except_clause or t == _with_statement or
        t == _conditional_expression or t == _match_statement or t == _case_clause;
}

inline fn isNesting(t: [*c]const u8) bool {
    return t == _if_statement or t == _for_statement or t == _while_statement or
        t == _try_statement or t == _with_statement or t == _match_statement;
}

inline fn isBooleanOp(t: [*c]const u8) bool {
    return t == _boolean_operator;
}

inline fn isHashSkip(t: [*c]const u8) bool {
    return t == _comment;
}

inline fn isHashNormalize(t: [*c]const u8) bool {
    return t == _identifier or t == _string or t == _string_content or
        t == _integer or t == _float or t == _none or t == _true or t == _false;
}

// --- JSON writer ---

const JsonWriter = struct {
    buf: [*]u8,
    cap: u32,
    len: u32,

    fn init(buf: [*]u8, cap: u32) JsonWriter {
        return .{ .buf = buf, .cap = cap, .len = 0 };
    }

    fn write(self: *JsonWriter, data: []const u8) void {
        const avail = if (self.len < self.cap) self.cap - self.len else 0;
        const to_copy = @min(data.len, avail);
        if (to_copy > 0) {
            @memcpy(self.buf[self.len .. self.len + to_copy], data[0..to_copy]);
        }
        self.len += @intCast(data.len);
    }

    fn writeByte(self: *JsonWriter, byte: u8) void {
        if (self.len < self.cap) {
            self.buf[self.len] = byte;
        }
        self.len += 1;
    }

    fn writeJsonString(self: *JsonWriter, s: []const u8) void {
        self.writeByte('"');
        for (s) |c| {
            switch (c) {
                '"' => self.write("\\\""),
                '\\' => self.write("\\\\"),
                '\n' => self.write("\\n"),
                '\r' => self.write("\\r"),
                '\t' => self.write("\\t"),
                else => self.writeByte(c),
            }
        }
        self.writeByte('"');
    }

    fn writeInt(self: *JsonWriter, val: i32) void {
        var buf: [12]u8 = undefined;
        var v: u32 = if (val < 0) blk: {
            self.writeByte('-');
            break :blk @intCast(@as(u32, @bitCast(-val)));
        } else @intCast(val);
        var i: usize = buf.len;
        while (v >= 10) {
            i -= 1;
            buf[i] = @intCast('0' + v % 10);
            v /= 10;
        }
        i -= 1;
        buf[i] = @intCast('0' + v);
        self.write(buf[i..]);
    }
};

// --- Metrics ---

const Metrics = struct {
    cc: i32 = 1,
    cog: i32 = 0,
    mnd: i32 = 0,
    loc: i32 = 0,
    pc: i32 = 0,
    rc: i32 = 0,
};

fn computeMetricsAndHash(func_node: ts.TSNode, source: [*]const u8) struct { m: Metrics, sh: u64, ch: u64 } {
    var m = Metrics{};
    m.loc = @as(i32, @intCast(ts.ts_node_end_point(func_node).row)) -
        @as(i32, @intCast(ts.ts_node_start_point(func_node).row)) + 1;

    // Parameter count
    const params = ts.ts_node_child_by_field_name(func_node, "parameters", 10);
    if (!ts.ts_node_is_null(params)) {
        m.pc = @intCast(ts.ts_node_named_child_count(params));
    }

    // Content hash
    const ch_start = ts.ts_node_start_byte(func_node);
    const ch_end = ts.ts_node_end_byte(func_node);
    var ch_h = std.hash.XxHash64.init(0);
    ch_h.update(source[ch_start..ch_end]);
    const ch = ch_h.final();

    // Fused walk: metrics on body + structure hash on full node
    var sh_h = std.hash.XxHash64.init(0);
    const body = ts.ts_node_child_by_field_name(func_node, "body", 4);
    const body_start = if (!ts.ts_node_is_null(body)) ts.ts_node_start_byte(body) else 0xFFFFFFFF;
    const body_end = if (!ts.ts_node_is_null(body)) ts.ts_node_end_byte(body) else 0;

    fusedWalk(func_node, &sh_h, &m, body_start, body_end, false, 0);

    return .{ .m = m, .sh = sh_h.final(), .ch = ch };
}

fn fusedWalk(
    node: ts.TSNode,
    sh: *std.hash.XxHash64,
    m: *Metrics,
    body_start: u32,
    body_end: u32,
    in_body: bool,
    nesting: i32,
) void {
    const t = ts.ts_node_type(node);
    if (isHashSkip(t)) return;
    if (isHashNormalize(t)) {
        sh.update("_");
        return;
    }
    sh.update(std.mem.span(t));
    sh.update("(");

    // Check if this node IS the body
    const node_start = ts.ts_node_start_byte(node);
    const node_end = ts.ts_node_end_byte(node);
    const now_in_body = in_body or (node_start == body_start and node_end == body_end);

    // Metrics (only within body)
    if (now_in_body and in_body) {
        if (isBranch(t)) {
            m.cc += 1;
            m.cog += 1 + nesting;
        }
        if (isBooleanOp(t)) {
            m.cc += 1;
            m.cog += 1;
        }
        if (t == _return_statement) {
            m.rc += 1;
        }
    }

    const child_nest = if (now_in_body and isNesting(t)) nesting + 1 else nesting;
    if (child_nest > m.mnd) m.mnd = child_nest;

    const n_children = ts.ts_node_child_count(node);
    for (0..n_children) |ci| {
        const child = ts.ts_node_child(node, @intCast(ci));
        const ct = ts.ts_node_type(child);
        if (isHashSkip(ct)) continue;

        // Skip nested functions/classes for metrics
        const skip_metrics = now_in_body and (isFunctionDef(ct) or isClassDef(ct) or isDecoratedDef(ct));
        fusedWalk(child, sh, m, body_start, body_end, now_in_body and !skip_metrics, child_nest);
    }

    sh.update(")");
}

fn structureHashOnly(node: ts.TSNode) u64 {
    var h = std.hash.XxHash64.init(0);
    structureHashWalk(node, &h);
    return h.final();
}

fn structureHashWalk(node: ts.TSNode, h: *std.hash.XxHash64) void {
    const t = ts.ts_node_type(node);
    if (isHashSkip(t)) return;
    if (isHashNormalize(t)) {
        h.update("_");
        return;
    }
    h.update(std.mem.span(t));
    h.update("(");
    const n = ts.ts_node_child_count(node);
    for (0..n) |ci| {
        const child = ts.ts_node_child(node, @intCast(ci));
        if (!isHashSkip(ts.ts_node_type(child))) {
            structureHashWalk(child, h);
        }
    }
    h.update(")");
}

// --- Helpers ---

fn nodeSlice(node: ts.TSNode, source: [*]const u8) []const u8 {
    return source[ts.ts_node_start_byte(node)..ts.ts_node_end_byte(node)];
}

fn writeHex64(val: u64, buf: *[16]u8) void {
    const hex = "0123456789abcdef";
    var v = val;
    var i: usize = 16;
    while (i > 0) {
        i -= 1;
        buf[i] = hex[@as(usize, @intCast(v & 0xf))];
        v >>= 4;
    }
}

fn hasSelfOrCls(func_node: ts.TSNode, source: [*]const u8) bool {
    const params = ts.ts_node_child_by_field_name(func_node, "parameters", 10);
    if (ts.ts_node_is_null(params)) return false;
    const pc = ts.ts_node_child_count(params);
    for (0..pc) |i| {
        const child = ts.ts_node_child(params, @intCast(i));
        if (isIdentifier(ts.ts_node_type(child))) {
            const text = nodeSlice(child, source);
            if (std.mem.eql(u8, text, "self") or std.mem.eql(u8, text, "cls"))
                return true;
        }
    }
    return false;
}

fn isUpperCase(s: []const u8) bool {
    if (s.len == 0) return false;
    for (s) |c| {
        if (c >= 'a' and c <= 'z') return false;
    }
    return true;
}

// Builtin check using hash set
const py_builtins = std.StaticStringMap(void).initComptime(.{
    .{ "print", {} },  .{ "len", {} },    .{ "range", {} },    .{ "enumerate", {} },
    .{ "zip", {} },    .{ "map", {} },    .{ "filter", {} },   .{ "isinstance", {} },
    .{ "issubclass", {} }, .{ "hasattr", {} }, .{ "getattr", {} }, .{ "setattr", {} },
    .{ "type", {} },   .{ "id", {} },     .{ "hash", {} },     .{ "repr", {} },
    .{ "str", {} },    .{ "int", {} },    .{ "float", {} },    .{ "bool", {} },
    .{ "bytes", {} },  .{ "list", {} },   .{ "dict", {} },     .{ "set", {} },
    .{ "tuple", {} },  .{ "sorted", {} }, .{ "reversed", {} }, .{ "min", {} },
    .{ "max", {} },    .{ "sum", {} },    .{ "abs", {} },      .{ "round", {} },
    .{ "open", {} },   .{ "iter", {} },   .{ "next", {} },     .{ "any", {} },
    .{ "all", {} },    .{ "super", {} },  .{ "property", {} }, .{ "staticmethod", {} },
    .{ "classmethod", {} }, .{ "ValueError", {} }, .{ "TypeError", {} },
    .{ "KeyError", {} }, .{ "AttributeError", {} }, .{ "RuntimeError", {} },
    .{ "Exception", {} }, .{ "NotImplementedError", {} }, .{ "frozenset", {} },
    .{ "delattr", {} }, .{ "StopIteration", {} }, .{ "OSError", {} },
    .{ "IOError", {} }, .{ "FileNotFoundError", {} }, .{ "ImportError", {} },
});

// --- Qualified name buffer ---

const NameBuf = struct {
    buf: [2048]u8 = undefined,
    len: usize = 0,

    fn set(self: *NameBuf, prefix: []const u8, name: []const u8) void {
        var i: usize = 0;
        const total = prefix.len + 1 + name.len;
        if (total > self.buf.len) return;
        @memcpy(self.buf[i .. i + prefix.len], prefix);
        i += prefix.len;
        self.buf[i] = '.';
        i += 1;
        @memcpy(self.buf[i .. i + name.len], name);
        self.len = total;
    }

    fn slice(self: *const NameBuf) []const u8 {
        return self.buf[0..self.len];
    }
};

// --- Entity output ---

fn writeEntity(w: *JsonWriter, kind: []const u8, name: []const u8, file_path: []const u8, node: ts.TSNode, _: [*]const u8, m: ?Metrics, ch: u64, sh: u64) void {
    var ch_hex: [16]u8 = undefined;
    var sh_hex: [16]u8 = undefined;
    writeHex64(ch, &ch_hex);
    writeHex64(sh, &sh_hex);

    w.write("{\"k\":\"n\",\"name\":");
    w.writeJsonString(name);
    w.write(",\"type\":");
    w.writeJsonString(kind);
    w.write(",\"file\":");
    w.writeJsonString(file_path);
    w.write(",\"line\":");
    w.writeInt(@intCast(ts.ts_node_start_point(node).row + 1));
    w.write(",\"end_line\":");
    w.writeInt(@intCast(ts.ts_node_end_point(node).row + 1));
    w.write(",\"ch\":\"");
    w.write(&ch_hex);
    w.write("\",\"sh\":\"");
    w.write(&sh_hex);
    w.writeByte('"');

    if (m) |met| {
        w.write(",\"cc\":");
        w.writeInt(met.cc);
        w.write(",\"cog\":");
        w.writeInt(met.cog);
        w.write(",\"mnd\":");
        w.writeInt(met.mnd);
        w.write(",\"loc\":");
        w.writeInt(met.loc);
        w.write(",\"pc\":");
        w.writeInt(met.pc);
        w.write(",\"rc\":");
        w.writeInt(met.rc);
    }
    w.write("}\n");
}

fn writeEdge(w: *JsonWriter, src: []const u8, rel: []const u8, tgt: []const u8, resolved: bool) void {
    w.write("{\"k\":\"e\",\"src\":");
    w.writeJsonString(src);
    w.write(",\"rel\":");
    w.writeJsonString(rel);
    w.write(",\"tgt\":");
    w.writeJsonString(tgt);
    if (!resolved) w.write(",\"unresolved\":true");
    w.write("}\n");
}

// --- Extraction ---

fn extractWalkBody(body_node: ts.TSNode, source: [*]const u8, parent_name: []const u8, file_path: []const u8, w: *JsonWriter) void {
    const cc = ts.ts_node_child_count(body_node);
    for (0..cc) |ci| {
        const child = ts.ts_node_child(body_node, @intCast(ci));
        const t = ts.ts_node_type(child);

        if (isFunctionDef(t)) {
            extractFunction(child, source, parent_name, file_path, w);
        } else if (isClassDef(t)) {
            extractClass(child, source, parent_name, file_path, w);
        } else if (isDecoratedDef(t)) {
            const defn = ts.ts_node_child_by_field_name(child, "definition", 10);
            if (!ts.ts_node_is_null(defn)) {
                const dt = ts.ts_node_type(defn);
                if (isFunctionDef(dt)) extractFunction(defn, source, parent_name, file_path, w);
                if (isClassDef(dt)) extractClass(defn, source, parent_name, file_path, w);
            }
        } else if (isExprStmt(t)) {
            extractAssignment(child, source, parent_name, file_path, w);
        }
    }
}

fn extractClass(node: ts.TSNode, source: [*]const u8, parent_name: []const u8, file_path: []const u8, w: *JsonWriter) void {
    const name_node = ts.ts_node_child_by_field_name(node, "name", 4);
    if (ts.ts_node_is_null(name_node)) return;
    var qname: NameBuf = .{};
    qname.set(parent_name, nodeSlice(name_node, source));

    const ch_start = ts.ts_node_start_byte(node);
    const ch_end = ts.ts_node_end_byte(node);
    var ch_h = std.hash.XxHash64.init(0);
    ch_h.update(source[ch_start..ch_end]);
    const sh = structureHashOnly(node);

    writeEntity(w, "class", qname.slice(), file_path, node, source, null, ch_h.final(), sh);
    writeEdge(w, parent_name, "contains", qname.slice(), true);

    // Inheritance
    const supers = ts.ts_node_child_by_field_name(node, "superclasses", 12);
    if (!ts.ts_node_is_null(supers)) {
        const sc = ts.ts_node_child_count(supers);
        for (0..sc) |i| {
            const arg = ts.ts_node_child(supers, @intCast(i));
            const at = ts.ts_node_type(arg);
            if (isIdentifier(at) or isAttribute(at)) {
                writeEdge(w, qname.slice(), "inherits", nodeSlice(arg, source), false);
            }
        }
    }

    const body = ts.ts_node_child_by_field_name(node, "body", 4);
    if (!ts.ts_node_is_null(body)) extractWalkBody(body, source, qname.slice(), file_path, w);
}

fn extractFunction(node: ts.TSNode, source: [*]const u8, parent_name: []const u8, file_path: []const u8, w: *JsonWriter) void {
    const name_node = ts.ts_node_child_by_field_name(node, "name", 4);
    if (ts.ts_node_is_null(name_node)) return;
    var qname: NameBuf = .{};
    qname.set(parent_name, nodeSlice(name_node, source));

    const is_method = hasSelfOrCls(node, source);
    const kind: []const u8 = if (is_method) "method" else "function";
    const result = computeMetricsAndHash(node, source);

    writeEntity(w, kind, qname.slice(), file_path, node, source, result.m, result.ch, result.sh);
    writeEdge(w, parent_name, "contains", qname.slice(), true);

    // Extract calls
    const body = ts.ts_node_child_by_field_name(node, "body", 4);
    if (!ts.ts_node_is_null(body)) {
        const class_name: ?[]const u8 = if (is_method) parent_name else null;
        extractCalls(body, source, qname.slice(), class_name, w);
    }
}

fn extractCalls(root: ts.TSNode, source: [*]const u8, caller: []const u8, class_name: ?[]const u8, w: *JsonWriter) void {
    const cc = ts.ts_node_child_count(root);
    for (0..cc) |ci| {
        const child = ts.ts_node_child(root, @intCast(ci));
        const t = ts.ts_node_type(child);

        if (isFunctionDef(t) or isClassDef(t) or isDecoratedDef(t)) continue;

        if (isCall(t)) {
            const func = ts.ts_node_child_by_field_name(child, "function", 8);
            if (!ts.ts_node_is_null(func)) {
                const ft = ts.ts_node_type(func);
                if (isIdentifier(ft)) {
                    const name = nodeSlice(func, source);
                    if (!py_builtins.has(name)) {
                        writeEdge(w, caller, "calls", name, false);
                    }
                } else if (isAttribute(ft)) {
                    const obj = ts.ts_node_child_by_field_name(func, "object", 6);
                    const attr = ts.ts_node_child_by_field_name(func, "attribute", 9);
                    if (!ts.ts_node_is_null(obj) and !ts.ts_node_is_null(attr)) {
                        if (isIdentifier(ts.ts_node_type(obj))) {
                            const obj_text = nodeSlice(obj, source);
                            const attr_text = nodeSlice(attr, source);
                            if ((std.mem.eql(u8, obj_text, "self") or std.mem.eql(u8, obj_text, "cls")) and class_name != null) {
                                var target: NameBuf = .{};
                                target.set(class_name.?, attr_text);
                                writeEdge(w, caller, "calls", target.slice(), true);
                            } else {
                                var target: NameBuf = .{};
                                target.set(obj_text, attr_text);
                                writeEdge(w, caller, "calls", target.slice(), false);
                            }
                        }
                    }
                }
            }
        }
        extractCalls(child, source, caller, class_name, w);
    }
}

fn extractAssignment(node: ts.TSNode, source: [*]const u8, parent_name: []const u8, file_path: []const u8, w: *JsonWriter) void {
    const cc = ts.ts_node_child_count(node);
    for (0..cc) |ci| {
        const child = ts.ts_node_child(node, @intCast(ci));
        if (!isAssignment(ts.ts_node_type(child))) continue;
        const left = ts.ts_node_child_by_field_name(child, "left", 4);
        if (ts.ts_node_is_null(left) or !isIdentifier(ts.ts_node_type(left))) continue;
        const name_text = nodeSlice(left, source);
        if (!isUpperCase(name_text)) continue;
        var qname: NameBuf = .{};
        qname.set(parent_name, name_text);

        var ch_h = std.hash.XxHash64.init(0);
        ch_h.update(source[ts.ts_node_start_byte(child)..ts.ts_node_end_byte(child)]);

        writeEntity(w, "constant", qname.slice(), file_path, child, source, null, ch_h.final(), structureHashOnly(child));
        writeEdge(w, parent_name, "contains", qname.slice(), true);
    }
}

fn extractImports(root: ts.TSNode, source: [*]const u8, module_name: []const u8, w: *JsonWriter) void {
    const cc = ts.ts_node_child_count(root);
    for (0..cc) |ci| {
        const child = ts.ts_node_child(root, @intCast(ci));
        const t = ts.ts_node_type(child);
        if (t == _import_statement) {
            const nc = ts.ts_node_child_count(child);
            for (0..nc) |i| {
                const name_node = ts.ts_node_child(child, @intCast(i));
                if (ts.ts_node_type(name_node) == _dotted_name) {
                    writeEdge(w, module_name, "imports", nodeSlice(name_node, source), false);
                }
            }
        } else if (t == _import_from_statement) {
            const mod_node = ts.ts_node_child_by_field_name(child, "module_name", 11);
            if (!ts.ts_node_is_null(mod_node)) {
                writeEdge(w, module_name, "imports", nodeSlice(mod_node, source), false);
            }
        }
    }
}

// --- Single-file export ---

export fn smg_extract_python(
    source: [*]const u8,
    source_len: u32,
    module_name: [*]const u8,
    module_name_len: u32,
    file_path: [*]const u8,
    file_path_len: u32,
    out_buf: [*]u8,
    out_buf_cap: u32,
) callconv(.c) u32 {
    const lang = tree_sitter_python();
    initTypePtrs(lang);

    const parser = ts.ts_parser_new() orelse return 0;
    defer ts.ts_parser_delete(parser);
    _ = ts.ts_parser_set_language(parser, lang);

    const tree = ts.ts_parser_parse_string(parser, null, source, source_len) orelse return 0;
    defer ts.ts_tree_delete(tree);

    const root = ts.ts_tree_root_node(tree);
    var w = JsonWriter.init(out_buf, out_buf_cap);

    extractWalkBody(root, source, module_name[0..module_name_len], file_path[0..file_path_len], &w);
    extractImports(root, source, module_name[0..module_name_len], &w);

    return w.len;
}

// --- Batch export (multi-file, threaded) ---

const FileEntry = extern struct {
    source: [*]const u8,
    source_len: u32,
    module_name: [*]const u8,
    module_name_len: u32,
    file_path: [*]const u8,
    file_path_len: u32,
    out_buf: [*]u8,
    out_buf_cap: u32,
    out_len: u32, // written by worker
};

export fn smg_extract_python_batch(
    entries: [*]FileEntry,
    count: u32,
    n_threads: u32,
) callconv(.c) void {
    const lang = tree_sitter_python();
    initTypePtrs(lang);

    if (n_threads <= 1 or count <= 1) {
        // Serial fallback
        for (0..count) |i| {
            entries[i].out_len = smg_extract_python(
                entries[i].source, entries[i].source_len,
                entries[i].module_name, entries[i].module_name_len,
                entries[i].file_path, entries[i].file_path_len,
                entries[i].out_buf, entries[i].out_buf_cap,
            );
        }
        return;
    }

    // Threaded extraction
    const actual_threads = @min(n_threads, count);
    var threads: [64]std.Thread = undefined;
    const chunk = (count + actual_threads - 1) / actual_threads;

    for (0..actual_threads) |ti| {
        const start: u32 = @intCast(ti * chunk);
        const end: u32 = @min(start + @as(u32, @intCast(chunk)), count);
        if (start >= end) continue;
        threads[ti] = std.Thread.spawn(.{}, workerFn, .{ entries, start, end, lang }) catch {
            // Fallback: do this chunk serially
            for (start..end) |i| {
                entries[i].out_len = smg_extract_python(
                    entries[i].source, entries[i].source_len,
                    entries[i].module_name, entries[i].module_name_len,
                    entries[i].file_path, entries[i].file_path_len,
                    entries[i].out_buf, entries[i].out_buf_cap,
                );
            }
            continue;
        };
    }

    for (0..actual_threads) |ti| {
        threads[ti].join();
    }
}

fn workerFn(entries: [*]FileEntry, start: u32, end: u32, lang: *ts.TSLanguage) void {
    const parser = ts.ts_parser_new() orelse return;
    defer ts.ts_parser_delete(parser);
    _ = ts.ts_parser_set_language(parser, lang);

    for (start..end) |i| {
        const e = &entries[i];
        const tree = ts.ts_parser_parse_string(parser, null, e.source, e.source_len) orelse {
            e.out_len = 0;
            continue;
        };
        defer ts.ts_tree_delete(tree);

        const root = ts.ts_tree_root_node(tree);
        var w = JsonWriter.init(e.out_buf, e.out_buf_cap);

        extractWalkBody(root, e.source, e.module_name[0..e.module_name_len], e.file_path[0..e.file_path_len], &w);
        extractImports(root, e.source, e.module_name[0..e.module_name_len], &w);

        e.out_len = w.len;
    }
}
