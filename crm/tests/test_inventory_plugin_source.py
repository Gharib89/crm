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


def test_a_name_qualified_from_an_imported_parent_namespace_is_a_use() -> None:
    assert set(_uses('var t = IO.File.Exists("p");', "using System;")) == {"System.IO.File"}


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
