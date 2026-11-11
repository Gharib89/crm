"""`type_uses`: where C# source creates or accesses a listed type (#1192, ADR 0034)."""

# pyright: basic

from __future__ import annotations

import pytest

from crm.core.inventory_plugin_source import type_uses

LISTED = frozenset(
    ("System.IO.File", "System.Data.SqlClient.SqlConnection", "Microsoft.Win32.Registry")
)


def _uses(body: str, usings: str = "") -> dict[str, list[tuple[int, int]]]:
    source = f"{usings}\nclass P {{ void M() {{\n{body}\n}} }}\n"
    found = type_uses(source.encode(), LISTED)
    assert found is not None
    return found


def test_an_object_creation_through_an_imported_namespace_is_a_use() -> None:
    found = _uses('var c = new SqlConnection("x");', "using System.Data.SqlClient;")
    assert found == {"System.Data.SqlClient.SqlConnection": [(3, 13)]}


def test_a_member_access_through_an_imported_namespace_is_a_use() -> None:
    assert _uses('var t = File.ReadAllText("p");', "using System.IO;") == {
        "System.IO.File": [(3, 9)]
    }


def test_a_fully_qualified_name_needs_no_using() -> None:
    found = _uses(
        'var t = System.IO.File.Exists("p"); var c = new System.Data.SqlClient.SqlConnection();'
    )
    assert set(found) == {"System.IO.File", "System.Data.SqlClient.SqlConnection"}


def test_a_global_qualified_name_is_a_use() -> None:
    assert set(_uses('var t = global::System.IO.File.Exists("p");')) == {"System.IO.File"}


def test_a_using_imports_types_not_child_namespaces() -> None:
    assert _uses('var t = IO.File.Exists("p");', "using System;") == {}


@pytest.mark.parametrize(
    "declaration",
    ["string File;", "string File { get; set; }"],
    ids=["field", "property"],
)
def test_a_member_named_like_a_type_hides_the_import(declaration: str) -> None:
    source = f"using System.IO;\nclass P {{ {declaration} void M() {{ File.Trim(); }} }}\n"
    assert type_uses(source.encode(), LISTED) == {}


def test_a_parameter_named_like_a_type_hides_the_import_in_its_method_only() -> None:
    source = (
        "using System.IO;\nclass P { void N(string File) { File.Trim(); } "
        'void M() { File.Delete("p"); } }\n'
    )
    found = type_uses(source.encode(), LISTED) or {}
    assert [len(sites) for sites in found.values()] == [1]


def test_a_local_variable_named_like_a_type_hides_the_import() -> None:
    assert _uses('var File = "x"; File.Trim();', "using System.IO;") == {}


def test_nameof_under_using_static_is_not_a_use() -> None:
    assert _uses("var n = nameof(n);", "using static System.IO.File;") == {}


def test_a_type_alias_is_a_use() -> None:
    found = _uses('var t = IOFile.Exists("p");', "using IOFile = System.IO.File;")
    assert found == {"System.IO.File": [(3, 9)]}


def test_a_namespace_alias_is_a_use() -> None:
    found = _uses("var c = new Sql.SqlConnection();", "using Sql = System.Data.SqlClient;")
    assert set(found) == {"System.Data.SqlClient.SqlConnection"}


def test_a_bare_call_under_using_static_is_a_use_of_the_imported_type() -> None:
    found = _uses('var t = ReadAllText("p");', "using static System.IO.File;")
    assert found == {"System.IO.File": [(3, 9)]}


def test_a_bare_call_to_a_method_the_file_declares_is_not_a_use() -> None:
    source = (
        "using static System.IO.File;\n"
        'class P { void ReadAllText(string p) {} void M() { ReadAllText("p"); } }\n'
    )
    assert type_uses(source.encode(), LISTED) == {}


def test_a_nested_member_access_reports_the_type_once() -> None:
    found = _uses('var k = Registry.CurrentUser.OpenSubKey("k");', "using Microsoft.Win32;")
    assert found == {"Microsoft.Win32.Registry": [(3, 9)]}


@pytest.mark.parametrize(
    "source",
    [
        "using System.IO;\nclass P { }\n",
        "using System.IO;\nclass P { void M() { /* File.Delete(p); new SqlConnection(); */ } }\n",
        "using System.IO;\n"
        'class P { string s = "File.Delete(p)"; string t = @"new SqlConnection()"; }\n',
        "using System.IO;\nclass File { public static void Delete(string p) {} }\n"
        'class P { void M() { File.Delete("p"); } }\n',
        "using System.IO;\n"
        'class P { void M() { var m = new MemoryStream(); Path.Combine("a", "b"); } }\n',
        'class P { void M() { File.Delete("p"); } }\n',
    ],
    ids=["unused-using", "comment", "string", "local-class", "unlisted-types", "no-using"],
)
def test_what_is_not_a_use_is_not_reported(source: str) -> None:
    assert type_uses(source.encode(), LISTED) == {}


def test_a_class_declared_locally_beats_the_import_but_not_a_qualified_name() -> None:
    source = (
        "using System.IO;\nclass File { public static void Delete(string p) {} }\n"
        'class P { void M() { File.Delete("p"); System.IO.File.Delete("p"); } }\n'
    )
    assert type_uses(source.encode(), LISTED) == {"System.IO.File": [(3, 40)]}


def test_a_syntax_error_is_none() -> None:
    assert type_uses(b"using System.IO;\nclass P { void M( { File.Delete(p); }\n", LISTED) is None


def test_the_column_counts_characters_not_bytes() -> None:
    source = 'using System.IO;\nclass P { void M() { var s = "é€"; File.Delete(s); } }\n'
    assert type_uses(source.encode(), LISTED) == {"System.IO.File": [(2, 36)]}


def test_a_member_of_one_class_does_not_hide_the_type_in_another() -> None:
    source = (
        'using System.IO;\nclass A { string File; }\nclass B { void M() { File.Delete("p"); } }\n'
    )
    assert set(type_uses(source.encode(), LISTED) or {}) == {"System.IO.File"}


def test_a_local_variable_hides_the_type_only_in_its_block() -> None:
    source = (
        "using System.IO;\n"
        'class P { void M() { { var File = "x"; File.Trim(); } File.Delete("p"); } }\n'
    )
    assert type_uses(source.encode(), LISTED) == {"System.IO.File": [(2, 55)]}


def test_nameof_of_a_type_member_is_not_a_use() -> None:
    assert _uses("var n = nameof(File.Delete);", "using System.IO;") == {}


def test_a_using_statement_variable_hides_the_type_only_in_its_statement() -> None:
    body = 'using (var File = new MemoryStream()) { File.Dispose(); } File.Delete("p");'
    assert _uses(body, "using System.IO;") == {"System.IO.File": [(3, 59)]}


def test_a_bare_static_member_under_using_static_is_a_use() -> None:
    found = _uses('var k = LocalMachine.OpenSubKey("k");', "using static Microsoft.Win32.Registry;")
    assert found == {"Microsoft.Win32.Registry": [(3, 9)]}


def test_every_listed_using_static_type_is_credited() -> None:
    usings = "using static System.IO.File;\nusing static Microsoft.Win32.Registry;"
    found = _uses('var t = ReadAllText("p");', usings)
    assert set(found) == {"System.IO.File", "Microsoft.Win32.Registry"}


@pytest.mark.parametrize(
    "body",
    ['SqlConnection c = new("x");', "System.Data.SqlClient.SqlConnection c = new();"],
    ids=["simple", "qualified"],
)
def test_a_target_typed_new_takes_its_type_from_the_declaration(body: str) -> None:
    found = _uses(body, "using System.Data.SqlClient;")
    assert set(found) == {"System.Data.SqlClient.SqlConnection"}


def test_a_target_typed_new_of_an_unlisted_type_is_not_a_use() -> None:
    assert _uses("MemoryStream m = new();", "using System.IO;") == {}


@pytest.mark.parametrize(
    "body",
    [
        "if (o is int Foo) { Foo.ToString(); }",
        'int.TryParse("1", out var Foo); Foo.ToString();',
        "try { } catch (System.Exception Foo) { Foo.ToString(); }",
        "foreach (var Foo in o) { Foo.ToString(); }",
        "Func<int, int> f = Foo => Foo.GetHashCode();",
    ],
    ids=["pattern", "out-var", "catch", "foreach", "lambda"],
)
def test_a_binding_named_like_a_name_under_using_static_is_not_a_use(body: str) -> None:
    source = f"using static System.IO.File;\nclass P {{ void M(object o) {{ {body} }} }}\n"
    assert type_uses(source.encode(), LISTED) == {}


def test_a_nullable_target_typed_new_is_a_use() -> None:
    assert set(_uses('SqlConnection? c = new("x");', "using System.Data.SqlClient;")) == {
        "System.Data.SqlClient.SqlConnection"
    }
