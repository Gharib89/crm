"""Unit tests for crm.core.workflow.validate_workflow_xaml (pure, offline)."""
# pyright: basic
from __future__ import annotations

from crm.core.workflow import validate_workflow_xaml

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_ATTRIBUTE_SET = ["cwx_name", "cwx_amount"]

# Real exported skeleton — anonymised (all GUIDs replaced with 0000…).
_VALID_EMPTY = (
    '<Activity x:Class="XrmWorkflow00000000000000000000000000000000"'
    ' xmlns="http://schemas.microsoft.com/netfx/2009/xaml/activities"'
    ' xmlns:mva="clr-namespace:Microsoft.VisualBasic.Activities;assembly=System.Activities,'
    ' Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"'
    ' xmlns:mxs="clr-namespace:Microsoft.Xrm.Sdk;assembly=Microsoft.Xrm.Sdk,'
    ' Version=9.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"'
    ' xmlns:mxswa="clr-namespace:Microsoft.Xrm.Sdk.Workflow.Activities;assembly=Microsoft.Xrm.Sdk.Workflow,'
    ' Version=9.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"'
    ' xmlns:scg="clr-namespace:System.Collections.Generic;assembly=mscorlib,'
    ' Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"'
    ' xmlns:srs="clr-namespace:System.Runtime.Serialization;assembly=System.Runtime.Serialization,'
    ' Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"'
    ' xmlns:this="clr-namespace:"'
    ' xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">\n'
    '  <x:Members>\n'
    '    <x:Property Name="InputEntities" Type="InArgument(scg:IDictionary(x:String, mxs:Entity))" />\n'
    '  </x:Members>\n'
    '  <mva:VisualBasic.Settings>Assembly references and imported namespaces for internal implementation</mva:VisualBasic.Settings>\n'
    '  <mxswa:Workflow />\n'
    '</Activity>'
)

# Skeleton with one well-formed SetEntityProperty step.
_VALID_WITH_STEP = (
    '<Activity x:Class="XrmWorkflow00000000000000000000000000000000"'
    ' xmlns="http://schemas.microsoft.com/netfx/2009/xaml/activities"'
    ' xmlns:mva="clr-namespace:Microsoft.VisualBasic.Activities;assembly=System.Activities,'
    ' Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"'
    ' xmlns:mxs="clr-namespace:Microsoft.Xrm.Sdk;assembly=Microsoft.Xrm.Sdk,'
    ' Version=9.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"'
    ' xmlns:mxswa="clr-namespace:Microsoft.Xrm.Sdk.Workflow.Activities;assembly=Microsoft.Xrm.Sdk.Workflow,'
    ' Version=9.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"'
    ' xmlns:scg="clr-namespace:System.Collections.Generic;assembly=mscorlib,'
    ' Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"'
    ' xmlns:srs="clr-namespace:System.Runtime.Serialization;assembly=System.Runtime.Serialization,'
    ' Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"'
    ' xmlns:this="clr-namespace:"'
    ' xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">\n'
    '  <x:Members>\n'
    '    <x:Property Name="InputEntities" Type="InArgument(scg:IDictionary(x:String, mxs:Entity))" />\n'
    '  </x:Members>\n'
    '  <mva:VisualBasic.Settings>Assembly references and imported namespaces for internal implementation</mva:VisualBasic.Settings>\n'
    '  <mxswa:Workflow>\n'
    '    <mxswa:SetEntityProperty Entity="someref" EntityName="cwx_ticket" Attribute="cwx_name" Value="x" />\n'
    '  </mxswa:Workflow>\n'
    '</Activity>'
)

# Drop a closing tag → malformed XML (no unbound-prefix issue — all xmlns declared).
_MALFORMED = (
    '<Activity x:Class="XrmWorkflow00000000000000000000000000000000"'
    ' xmlns="http://schemas.microsoft.com/netfx/2009/xaml/activities"'
    ' xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">\n'
    '  <Sequence>\n'
    # deliberately missing </Sequence> and </Activity>
)

# Remove xmlns:mxswa but keep mxswa: elements — unbound prefix.
_MISSING_NAMESPACE = (
    '<Activity x:Class="XrmWorkflow00000000000000000000000000000000"'
    ' xmlns="http://schemas.microsoft.com/netfx/2009/xaml/activities"'
    ' xmlns:mva="clr-namespace:Microsoft.VisualBasic.Activities;assembly=System.Activities,'
    ' Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"'
    ' xmlns:mxs="clr-namespace:Microsoft.Xrm.Sdk;assembly=Microsoft.Xrm.Sdk,'
    ' Version=9.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"'
    # mxswa declaration deliberately removed
    ' xmlns:scg="clr-namespace:System.Collections.Generic;assembly=mscorlib,'
    ' Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"'
    ' xmlns:srs="clr-namespace:System.Runtime.Serialization;assembly=System.Runtime.Serialization,'
    ' Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"'
    ' xmlns:this="clr-namespace:"'
    ' xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">\n'
    '  <mxswa:Workflow />\n'
    '</Activity>'
)

# Undeclared prefix whose name sorts lexically AFTER "xmlns" (regression guard:
# the raw-text prefix scan must not mistake the literal "xmlns" for the culprit).
_UNDECLARED_PREFIX_SORTS_AFTER_XMLNS = (
    '<Activity x:Class="XrmWorkflow00000000000000000000000000000000"'
    ' xmlns="http://schemas.microsoft.com/netfx/2009/xaml/activities"'
    ' xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">\n'
    '  <zzz:Workflow />\n'  # zzz declaration deliberately absent
    '</Activity>'
)

# Undeclared prefix containing an NCName-legal hyphen (regression guard: the
# raw-text scan must still name it, not fall back to the generic "(unknown)").
_UNDECLARED_HYPHENATED_PREFIX = (
    '<Activity x:Class="XrmWorkflow00000000000000000000000000000000"'
    ' xmlns="http://schemas.microsoft.com/netfx/2009/xaml/activities"'
    ' xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">\n'
    '  <my-ns:Workflow />\n'  # my-ns declaration deliberately absent
    '</Activity>'
)

# Unknown activity name not in the allowlist.
_UNKNOWN_ACTIVITY = (
    '<Activity x:Class="XrmWorkflow00000000000000000000000000000000"'
    ' xmlns="http://schemas.microsoft.com/netfx/2009/xaml/activities"'
    ' xmlns:mva="clr-namespace:Microsoft.VisualBasic.Activities;assembly=System.Activities,'
    ' Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"'
    ' xmlns:mxs="clr-namespace:Microsoft.Xrm.Sdk;assembly=Microsoft.Xrm.Sdk,'
    ' Version=9.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"'
    ' xmlns:mxswa="clr-namespace:Microsoft.Xrm.Sdk.Workflow.Activities;assembly=Microsoft.Xrm.Sdk.Workflow,'
    ' Version=9.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"'
    ' xmlns:scg="clr-namespace:System.Collections.Generic;assembly=mscorlib,'
    ' Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"'
    ' xmlns:srs="clr-namespace:System.Runtime.Serialization;assembly=System.Runtime.Serialization,'
    ' Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"'
    ' xmlns:this="clr-namespace:"'
    ' xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">\n'
    '  <mxswa:Workflow>\n'
    '    <mxswa:Bogus />\n'
    '  </mxswa:Workflow>\n'
    '</Activity>'
)

# SetEntityProperty with an Attribute value not in _ATTRIBUTE_SET.
_BAD_ATTRIBUTE = (
    '<Activity x:Class="XrmWorkflow00000000000000000000000000000000"'
    ' xmlns="http://schemas.microsoft.com/netfx/2009/xaml/activities"'
    ' xmlns:mva="clr-namespace:Microsoft.VisualBasic.Activities;assembly=System.Activities,'
    ' Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"'
    ' xmlns:mxs="clr-namespace:Microsoft.Xrm.Sdk;assembly=Microsoft.Xrm.Sdk,'
    ' Version=9.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"'
    ' xmlns:mxswa="clr-namespace:Microsoft.Xrm.Sdk.Workflow.Activities;assembly=Microsoft.Xrm.Sdk.Workflow,'
    ' Version=9.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"'
    ' xmlns:scg="clr-namespace:System.Collections.Generic;assembly=mscorlib,'
    ' Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"'
    ' xmlns:srs="clr-namespace:System.Runtime.Serialization;assembly=System.Runtime.Serialization,'
    ' Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"'
    ' xmlns:this="clr-namespace:"'
    ' xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">\n'
    '  <mxswa:Workflow>\n'
    '    <mxswa:SetEntityProperty Entity="someref" EntityName="cwx_ticket"'
    ' Attribute="cwx_nonexistent" Value="x" />\n'
    '  </mxswa:Workflow>\n'
    '</Activity>'
)

# SetEntityProperty with Value supplied as a property-element child
# (<mxswa:SetEntityProperty.Value>…</mxswa:SetEntityProperty.Value>) instead of
# an XML attribute.  The other required args (Entity, EntityName, Attribute) are
# XML attributes; Attribute references a name that IS in _ATTRIBUTE_SET.
_PROPERTY_ELEMENT_VALUE = (
    '<Activity x:Class="XrmWorkflow00000000000000000000000000000000"'
    ' xmlns="http://schemas.microsoft.com/netfx/2009/xaml/activities"'
    ' xmlns:mva="clr-namespace:Microsoft.VisualBasic.Activities;assembly=System.Activities,'
    ' Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"'
    ' xmlns:mxs="clr-namespace:Microsoft.Xrm.Sdk;assembly=Microsoft.Xrm.Sdk,'
    ' Version=9.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"'
    ' xmlns:mxswa="clr-namespace:Microsoft.Xrm.Sdk.Workflow.Activities;assembly=Microsoft.Xrm.Sdk.Workflow,'
    ' Version=9.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"'
    ' xmlns:scg="clr-namespace:System.Collections.Generic;assembly=mscorlib,'
    ' Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"'
    ' xmlns:srs="clr-namespace:System.Runtime.Serialization;assembly=System.Runtime.Serialization,'
    ' Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"'
    ' xmlns:this="clr-namespace:"'
    ' xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">\n'
    '  <mxswa:Workflow>\n'
    '    <mxswa:SetEntityProperty Entity="someref" EntityName="cwx_ticket"'
    ' Attribute="cwx_name">\n'
    '      <mxswa:SetEntityProperty.Value>\n'
    '        <mxs:Entity />\n'
    '      </mxswa:SetEntityProperty.Value>\n'
    '    </mxswa:SetEntityProperty>\n'
    '  </mxswa:Workflow>\n'
    '</Activity>'
)

# SetEntityProperty missing its required "Entity" argument.
_MISSING_ARG = (
    '<Activity x:Class="XrmWorkflow00000000000000000000000000000000"'
    ' xmlns="http://schemas.microsoft.com/netfx/2009/xaml/activities"'
    ' xmlns:mva="clr-namespace:Microsoft.VisualBasic.Activities;assembly=System.Activities,'
    ' Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"'
    ' xmlns:mxs="clr-namespace:Microsoft.Xrm.Sdk;assembly=Microsoft.Xrm.Sdk,'
    ' Version=9.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"'
    ' xmlns:mxswa="clr-namespace:Microsoft.Xrm.Sdk.Workflow.Activities;assembly=Microsoft.Xrm.Sdk.Workflow,'
    ' Version=9.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35"'
    ' xmlns:scg="clr-namespace:System.Collections.Generic;assembly=mscorlib,'
    ' Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"'
    ' xmlns:srs="clr-namespace:System.Runtime.Serialization;assembly=System.Runtime.Serialization,'
    ' Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"'
    ' xmlns:this="clr-namespace:"'
    ' xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">\n'
    '  <mxswa:Workflow>\n'
    # Entity attribute removed — required arg missing
    '    <mxswa:SetEntityProperty EntityName="cwx_ticket" Attribute="cwx_name" Value="x" />\n'
    '  </mxswa:Workflow>\n'
    '</Activity>'
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestValidateWorkflowXaml:
    def test_valid_empty_no_warnings(self):
        """The real exported skeleton with no steps must produce zero warnings."""
        warnings = validate_workflow_xaml(_VALID_EMPTY, _ATTRIBUTE_SET)
        assert warnings == []

    def test_valid_with_step_no_warnings(self):
        """A well-formed SetEntityProperty step with a known attribute is clean."""
        warnings = validate_workflow_xaml(_VALID_WITH_STEP, _ATTRIBUTE_SET)
        assert warnings == []

    def test_malformed_xml_emits_malformed_warning(self):
        """Unclosed tags → 'malformed XAML: …' warning; further checks are skipped."""
        warnings = validate_workflow_xaml(_MALFORMED, _ATTRIBUTE_SET)
        assert len(warnings) == 1
        assert warnings[0].startswith("malformed XAML:")

    def test_missing_namespace_names_prefix_sorting_after_xmlns(self):
        """The reported prefix is the real culprit, even when it sorts after 'xmlns'.

        Regression: the raw-text scan used to leak the literal 'xmlns' into the
        candidate set, so any undeclared prefix sorting after it (e.g. 'zzz') was
        misreported as 'xmlns'.
        """
        warnings = validate_workflow_xaml(
            _UNDECLARED_PREFIX_SORTS_AFTER_XMLNS, _ATTRIBUTE_SET
        )
        assert len(warnings) == 1
        assert "zzz" in warnings[0]
        assert "'xmlns'" not in warnings[0]

    def test_undeclared_hyphenated_prefix_is_named(self):
        """An undeclared prefix with an NCName hyphen is named, not '(unknown)'."""
        warnings = validate_workflow_xaml(
            _UNDECLARED_HYPHENATED_PREFIX, _ATTRIBUTE_SET
        )
        assert len(warnings) == 1
        assert "my-ns" in warnings[0]
        assert "(unknown)" not in warnings[0]

    def test_missing_namespace_emits_undeclared_prefix_warning(self):
        """An mxswa: element with no xmlns:mxswa → 'undeclared namespace prefix: …'."""
        warnings = validate_workflow_xaml(_MISSING_NAMESPACE, _ATTRIBUTE_SET)
        assert len(warnings) == 1
        assert "undeclared namespace prefix" in warnings[0]
        assert "mxswa" in warnings[0]

    def test_malformed_and_missing_namespace_produce_distinct_warnings(self):
        """Check #1 (malformed) and check #2 (undeclared prefix) use different text."""
        malformed_w = validate_workflow_xaml(_MALFORMED, _ATTRIBUTE_SET)[0]
        missing_ns_w = validate_workflow_xaml(_MISSING_NAMESPACE, _ATTRIBUTE_SET)[0]
        assert malformed_w != missing_ns_w
        assert "malformed XAML" in malformed_w
        assert "undeclared namespace prefix" in missing_ns_w

    def test_unknown_activity_emits_warning(self):
        """An element in the mxswa namespace not on the allowlist → 'unknown activity: …'."""
        warnings = validate_workflow_xaml(_UNKNOWN_ACTIVITY, _ATTRIBUTE_SET)
        assert any("unknown activity" in w and "Bogus" in w for w in warnings)

    def test_bad_attribute_emits_warning(self):
        """An Attribute= value absent from attribute_set → 'attribute not found on entity: …'."""
        warnings = validate_workflow_xaml(_BAD_ATTRIBUTE, _ATTRIBUTE_SET)
        assert any("attribute not found on entity" in w and "cwx_nonexistent" in w for w in warnings)

    def test_missing_required_arg_emits_warning(self):
        """SetEntityProperty missing Entity → 'SetEntityProperty missing required argument: Entity'."""
        warnings = validate_workflow_xaml(_MISSING_ARG, _ATTRIBUTE_SET)
        assert any(
            "SetEntityProperty" in w and "missing required argument" in w and "Entity" in w
            for w in warnings
        )

    def test_returns_list_not_raises_on_reference_problems(self):
        """The function never raises on reference problems — only collects warnings."""
        # All the bad fixtures must return a list (never raise).
        for xaml in (_MALFORMED, _MISSING_NAMESPACE, _UNKNOWN_ACTIVITY, _BAD_ATTRIBUTE, _MISSING_ARG):
            result = validate_workflow_xaml(xaml, _ATTRIBUTE_SET)
            assert isinstance(result, list)

    def test_accepts_generator_as_attribute_set(self):
        """attribute_set is Iterable[str] — a generator must work."""
        gen = (a for a in _ATTRIBUTE_SET)
        warnings = validate_workflow_xaml(_VALID_WITH_STEP, gen)
        assert warnings == []

    def test_empty_xaml_emits_malformed_warning(self):
        """Completely empty string → malformed XAML warning."""
        warnings = validate_workflow_xaml("", _ATTRIBUTE_SET)
        assert len(warnings) == 1
        assert "malformed XAML" in warnings[0]

    def test_property_element_child_not_flagged_as_unknown_activity(self):
        """SetEntityProperty with Value as a property-element child must produce no warnings.

        Real XAML serializes some arguments as <mxswa:SetEntityProperty.Value>…
        child elements instead of XML attributes.  The dot in the local name
        marks these as property-elements — they must not be treated as unknown
        activities (check 3) and the required 'Value' arg is satisfied by the
        child element (check 5).
        """
        warnings = validate_workflow_xaml(_PROPERTY_ELEMENT_VALUE, _ATTRIBUTE_SET)
        assert warnings == []
