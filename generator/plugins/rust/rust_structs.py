# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

from typing import Dict, Iterable, List, Optional, Tuple

import generator.model as model

from .rust_commons import (
    TypeData,
    generate_extras,
    generate_literal_struct_name,
    generate_property,
    get_extended_properties,
    get_from_name,
    get_message_type_name,
    get_name,
    get_type_name,
    struct_wrapper,
    type_alias_wrapper,
    fix_lsp_method_name,
)
from .rust_lang_utils import get_parts, lines_to_doc_comments, to_upper_camel_case


def generate_type_aliases(spec: model.LSPModel, types: TypeData) -> None:
    for alias in spec.typeAliases:
        if not types.has_id(alias):
            generate_type_alias(alias, types, spec)


def _get_doc(doc: Optional[str]) -> str:
    if doc:
        return lines_to_doc_comments(doc.splitlines(keepends=False))
    return []


def _is_some_array_type(items: Iterable[model.LSP_TYPE_SPEC]) -> bool:
    items_list = list(items)
    assert len(items_list) == 2
    item1, item2 = items_list

    if item1.kind == "array" and item2.kind == "reference":
        return item1.element.kind == "reference" and item1.element.name == item2.name

    if item2.kind == "array" and item1.kind == "reference":
        return item2.element.kind == "reference" and item2.element.name == item1.name
    return False


def _get_some_array_code(
    items: Iterable[model.LSP_TYPE_SPEC],
    types: Dict[str, List[str]],
    spec: model.LSPModel,
) -> List[str]:
    assert _is_some_array_type(items)
    items_list = list(items)
    item1 = items_list[0]
    item2 = items_list[1]

    if item1.kind == "array" and item2.kind == "reference":
        return [
            f"    One({get_type_name(item2, types, spec)}),",
            f"    Many({get_type_name(item1, types, spec)}),",
        ]

    if item2.kind == "array" and item1.kind == "reference":
        return [
            f"    One({get_type_name(item1, types, spec)}),",
            f"    Many({get_type_name(item2, types, spec)}),",
        ]
    return []


def _get_common_name(items: Iterable[model.LSP_TYPE_SPEC], kind: str) -> List[str]:
    names = [get_parts(item.name) for item in list(items) if item.kind == kind]
    if len(names) < 2:
        return []

    smallest = min(names, key=len)
    common = []
    for i in range(len(smallest)):
        if all(name[i] == smallest[i] for name in names):
            common.append(smallest[i])
    return common


def _is_all_reference_similar_type(alias: model.TypeAlias) -> bool:
    items_list = list(alias.type.items)
    return all(item.kind in ["reference", "base", "literal"] for item in items_list)


def _get_all_reference_similar_code(
    alias: model.TypeAlias,
    types: TypeData,
    spec: model.LSPModel,
) -> Tuple[List[str], bool]:
    items = alias.type.items
    assert _is_all_reference_similar_type(alias)

    # Ensure all literal types have a name
    for item in list(items):
        if item.kind == "literal":
            get_type_name(item, types, spec, None, alias.name)

    common_name = [
        i.lower()
        for i in (
            _get_common_name(items, "reference")
            + _get_common_name(items, "literal")
            + ["struct"]
        )
    ]

    lines = []
    value = 0
    field_names = []
    defaultable = False
    for item in list(items):
        if item.kind == "base" and item.name == "null":
            defaultable = True
            lines += ["#[default]"]
            lines += ["None,"]
            field_names += ["None"]
        elif item.kind == "base":
            name = _base_to_field_name(item.name)
            lines += [f"{name}({get_type_name(item, types, spec)}),"]
            field_names += [name]
        elif item.kind == "reference":
            name = [
                part for part in get_parts(item.name) if part.lower() not in common_name
            ]
            if len(name) == 0:
                name = [f"Value{value}"]
                value += 1
            common_name += [n.lower() for n in name]
            name = to_upper_camel_case("".join(name))
            field_names += [name]
            lines += [f"{name}({get_type_name(item, types, spec)}),"]
        elif item.kind == "literal":
            name = [
                part for part in get_parts(item.name) if part.lower() not in common_name
            ]
            optional_props = [p for p in item.value.properties if p.optional]
            required_props = [p for p in item.value.properties if not p.optional]

            # Try picking a name using required props first and then optional props
            if len(name) == 0:
                for p in required_props + optional_props:
                    name = [
                        part
                        for part in get_parts(p.name)
                        if part.lower() not in common_name
                    ]
                    if len(name) != 0:
                        break

            # If we still don't have a name, then try picking a name using required props
            # and then optional props without checking for common name list. But check
            # that the name is not already used.
            if len(name) == 0:
                for p in required_props + optional_props:
                    if to_upper_camel_case(p.name) not in field_names:
                        name = get_parts(p.name)
                        break

            # If we still don't have a name, then just use a generic "Value{int}" as name
            if len(name) == 0:
                name = [f"Value{value}"]
                value += 1
            common_name += [n.lower() for n in name]
            name = to_upper_camel_case("".join(name))
            field_names += [name]
            lines += [f"{name}({item.name}),"]
        else:
            raise ValueError(f"Unknown type {item}")
    return lines, defaultable


def _base_to_field_name(base_name: str) -> str:
    if base_name == "boolean":
        return "Bool"
    if base_name == "integer":
        return "Int"
    if base_name == "decimal":
        return "Real"
    if base_name == "string":
        return "String"
    if base_name == "uinteger":
        return "UInt"
    if base_name == "null":
        return "None"
    raise ValueError(f"Unknown base type {base_name}")


def _get_literal_field_name(literal: model.LiteralType, types: TypeData) -> str:
    properties = list(literal.value.properties)

    if len(properties) == 1 and properties[0].kind == "base":
        return _base_to_field_name(properties[0].name)

    if len(properties) == 1 and properties[0].kind == "reference":
        return to_upper_camel_case(properties[0].name)

    return generate_literal_struct_name(literal, types)


def _generate_or_type_alias(
    alias_def: model.TypeAlias, types: Dict[str, List[str]], spec: model.LSPModel
) -> List[str]:
    inner = []

    defaultable = False
    if len(alias_def.type.items) == 2 and _is_some_array_type(alias_def.type.items):
        inner += _get_some_array_code(alias_def.type.items, types, spec)
    elif _is_all_reference_similar_type(alias_def):
        result = _get_all_reference_similar_code(alias_def, types, spec)
        inner += result[0]
        defaultable = result[1]
    else:
        index = 0

        for sub_type in alias_def.type.items:
            if sub_type.kind == "base" and sub_type.name == "null":
                inner += "#[default]"
                inner += ["None,"]
                defaultable = True
            else:
                inner += [f"ValueType{index}({get_type_name(sub_type, types, spec)}),"]
            index += 1
    return type_alias_wrapper(alias_def, inner, defaultable)


def generate_type_alias(
    alias_def: model.TypeAlias, types: TypeData, spec: model.LSPModel
) -> List[str]:
    doc = _get_doc(alias_def.documentation)
    doc += generate_extras(alias_def)

    lines = []
    if alias_def.type.kind == "reference":
        lines += doc
        lines += [f"pub type {alias_def.name} = {alias_def.type.name};"]
    elif alias_def.type.kind == "array":
        lines += doc
        lines += [
            f"pub type {alias_def.name} = {get_type_name(alias_def.type, types, spec)};"
        ]
    elif alias_def.type.kind == "or":
        lines += _generate_or_type_alias(alias_def, types, spec)
    elif alias_def.type.kind == "and":
        raise ValueError("And type not supported")
    elif alias_def.type.kind == "literal":
        lines += doc
        lines += [
            f"pub type {alias_def.name} = {get_type_name(alias_def.type, types, spec)};"
        ]
    elif alias_def.type.kind == "base":
        lines += doc
        lines += [
            f"pub type {alias_def.name} = {get_type_name(alias_def.type, types, spec)};"
        ]
    else:
        pass

    types.add_type_info(alias_def, alias_def.name, lines)


def generate_structures(spec: model.LSPModel, types: TypeData) -> Dict[str, List[str]]:
    for struct in spec.structures:
        if not types.has_id(struct):
            generate_struct(struct, types, spec)
    return types


def generate_struct(
    struct_def: model.Structure, types: TypeData, spec: model.LSPModel
) -> None:
    inner = []
    for prop_def in get_extended_properties(struct_def, spec):
        inner += generate_property(prop_def, types, spec)

    lines = struct_wrapper(struct_def, inner, types, spec)
    types.add_type_info(struct_def, struct_def.name, lines)


def generate_notifications(
    spec: model.LSPModel, types: TypeData
) -> Dict[str, List[str]]:
    types.add_type_info(
        model.ReferenceType(kind="reference", name="Notification"), "Notification", [
            "pub trait Notification {",
            "    type Params: DeserializeOwned + Serialize + Send + Sync;",
            "    const METHOD: LSPNotificationMethods;",
            "}",
            "",
            "/// A JSON-RPC notification message.",
            "#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]",
            "pub struct NotificationMessage {",
            "    /// The version of the JSON-RPC protocol.",
            "    jsonrpc: Version,",
            "    /// The method to be invoked.",
            "    method: LSPNotificationMethods,",
            "    /// The method's params.",
            '    #[serde(skip_serializing_if = "Option::is_none")]',
            "    params: Option<LSPAny>,",
            "}",
            "",
            "impl NotificationMessage {",
            "    /// Constructs a JSON-RPC notification message object from its corresponding LSP type.",
            "    pub fn from_notification<R: Notification>(params: R::Params) -> Self",
            "    {",
            "        // This must always be either an Array or an Object. This will be guaranteed by the LSP,",
            "        // as to conform to the JSON-RPC spec.",
            '        let params = serde_json::to_value(params).expect("Notification parameters should be serializable.");',
            "",
            "        Self {",
            "            jsonrpc: Version,",
            "            method: R::METHOD,",
            "            params: Some(params),",
            "        }",
            "    }",
            "}",
        ]
    )
    for notification in spec.notifications:
        if not types.has_id(notification):
            generate_notification(notification, types, spec)
    return types


def required_rpc_properties(name: Optional[str] = None) -> List[model.Property]:
    props = [
        model.Property(
            name="jsonrpc",
            type=model.ReferenceType(kind="reference", name="Version"),
            optional=False,
            documentation="The version of the JSON RPC protocol.",
        ),
    ]
    if name:
        props += [
            model.Property(
                name="method",
                type=model.ReferenceType(kind="reference", name=name),
                optional=False,
                documentation="The method to be invoked.",
            ),
        ]
    return props


def generate_notification(
    notification_def: model.Notification, types: TypeData, spec: model.LSPModel
) -> None:
    name = get_name(notification_def)
    doc = _get_doc(notification_def.documentation)
    extras = generate_extras(notification_def)
    params = get_type_name(notification_def.params, types, spec) if notification_def.params else "LSPNull"
    lines = doc + extras + [
        "#[derive(Debug)]",
        f"pub struct {name};",
        "",
        f"impl Notification for {name} {{",
        f"    const METHOD: LSPNotificationMethods = LSPNotificationMethods::{fix_lsp_method_name(notification_def.method)};",
        f"    type Params = {params};",
        "}",
    ]
    types.add_type_info(
        notification_def, get_message_type_name(notification_def), lines
    )


def generate_required_request_types(
    spec: model.LSPModel, types: TypeData
) -> Dict[str, List[str]]:
    lsp_id = model.TypeAlias(
        name="LSPId",
        documentation="An identifier to denote a specific request.",
        type=model.OrType(
            kind="or",
            items=[
                model.BaseType(kind="base", name="integer"),
                model.BaseType(kind="base", name="string"),
            ],
        ),
    )
    generate_type_alias(lsp_id, types, spec)

    lsp_id_optional = model.TypeAlias(
        name="LSPIdOptional",
        documentation="An identifier to denote a specific response.",
        type=model.OrType(
            kind="or",
            items=[
                model.BaseType(kind="base", name="integer"),
                model.BaseType(kind="base", name="string"),
                model.BaseType(kind="base", name="null"),
            ],
        ),
    )
    generate_type_alias(lsp_id_optional, types, spec)

    types.add_type_info(
        model.ReferenceType(kind="reference", name="Version"),
        "Version",
        [
            "#[derive(Clone, Debug, PartialEq, Eq, Copy, Default)]",
            "struct Version;",
            "",
            "impl<'de> Deserialize<'de> for Version {",
                "fn deserialize<D>(deserializer: D) -> std::result::Result<Self, D::Error>",
                "where",
                    "D: Deserializer<'de>,",
                "{",
                    "#[derive(Deserialize)]",
            "struct Inner<'a>(#[serde(borrow)] std::borrow::Cow<'a, str>);",
            "",
                    "let Inner(ver) = Inner::deserialize(deserializer)?;",
            "",
                    "match ver.as_ref() {",
                        '"2.0" => Ok(Version),',
            r'_ => Err(serde::de::Error::custom("expected JSON-RPC version \"2.0\"")),',
                    "}",
                "}",
            "}",
            "",
            "impl Serialize for Version {",
                "fn serialize<S>(&self, serializer: S) -> std::result::Result<S::Ok, S::Error>",
                "where",
                    "S: Serializer,",
                "{",
                    'serializer.serialize_str("2.0")',
                "}",
            "}",
        ],
    )

    types.add_type_info(
        model.ReferenceType(kind="reference", name="Payload"),
        "Payload",
        [
            "#[derive(Clone, PartialEq, Eq, Deserialize, Serialize, Debug)]",
            "#[serde(untagged)]",
            "enum Payload {",
            "    Ok { result: LSPAny },",
            "    Err { error: ResponseError },",
            "}",
        ],
    )

    types.add_type_info(
        model.ReferenceType(kind="reference", name="ResponseMessage"), "ResponseMessage", [
            "/// A JSON-RPC response message.",
            "#[derive(Clone, PartialEq, Deserialize, Serialize)]",
            "pub struct ResponseMessage {",
            "    jsonrpc: Version,",
            "    id: LSPIdOptional,",
            "    #[serde(flatten)]",
            "    payload: Payload,",
            "}",
            "",
            "impl ResponseMessage {",
            "    pub fn from_ok(id: LSPIdOptional, result: LSPAny) -> Self {",
            "        Self {",
            "            jsonrpc: Version,",
            "            id,",
            "            payload: Payload::Ok { result },",
            "        }",
            "    }",
            "",
            "    pub fn from_error(id: LSPIdOptional, error: ResponseError) -> Self {",
            "        Self {",
            "            jsonrpc: Version,",
            "            id,",
            "            payload: Payload::Err { error },",
            "        }",
            "    }"
            "}",
        ]
    )

    types.add_type_info(
        model.ReferenceType(kind="reference", name="ResponseError"),
        "ResponseError",
        [
            "#[derive(Clone, PartialEq, Eq, Deserialize, Serialize, Debug)]",
            "pub struct ResponseError {",
            "    pub code: OR2<ErrorCodes, LSPErrorCodes>,",
            "    pub message: String,",
            '    #[serde(skip_serializing_if = "Option::is_none")]',
            "    pub data: Option<LSPAny>,",
            "}",
        ],
    )


def generate_requests(spec: model.LSPModel, types: TypeData) -> Dict[str, List[str]]:
    types.add_type_info(
        model.ReferenceType(kind="reference", name="Request"), "Request", [
            "pub trait Request {",
            "    type Params: DeserializeOwned + Serialize + Send + Sync;",
            "    type Result: DeserializeOwned + Serialize + Send + Sync;",
            "    const METHOD: LSPRequestMethods;",
            "}",
            "",
            "/// A JSON-RPC request message.",
            "#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]",
            "pub struct RequestMessage {",
            "    /// The version of the JSON-RPC protocol.",
            "    jsonrpc: Version,",
            "    /// The request id.",
            "    id: LSPId,",
            "    /// The method to be invoked.",
            "    method: LSPRequestMethods,",
            "    /// The method's params.",
            '    #[serde(skip_serializing_if = "Option::is_none")]',
            "    params: Option<LSPAny>,",
            "}",
            "",
            "impl RequestMessage {",
            "    /// Constructs a JSON-RPC request message object from its corresponding LSP type.",
            "    pub fn from_request<R: Request>(id: LSPId, params: R::Params) -> Self",
            "    {",
            "        // This must always be either an Array or an Object. This will be guaranteed by the LSP,",
            "        // as to conform to the JSON-RPC spec.",
            '        let params = serde_json::to_value(params).expect("Request parameters should be serializable.");',
            "",
            "        Self {",
            "            jsonrpc: Version,",
            "            id,",
            "            method: R::METHOD,",
            "            params: Some(params),",
            "        }",
            "    }",
            "}",
        ]
    )
    generate_required_request_types(spec, types)
    for request in spec.requests:
        if not types.has_id(request):
            generate_request(request, types, spec)
            generate_partial_result(request, types, spec)
            generate_registration_options(request, types, spec)
    return types


def generate_request(
    request_def: model.Request, types: TypeData, spec: model.LSPModel
) -> None:
    name = get_name(request_def)
    doc = _get_doc(request_def.documentation)
    extras = generate_extras(request_def)
    params = get_type_name(request_def.params, types, spec) if request_def.params else "LSPNull"
    result = get_type_name(request_def.result, types, spec) if request_def.params else "LSPNull"
    lines = doc + extras + [
        "#[derive(Debug)]",
        f"pub struct {name};",
        "",
        f"impl Request for {name} {{",
        f"    const METHOD: LSPRequestMethods = LSPRequestMethods::{fix_lsp_method_name(request_def.method)};",
        f"    type Params = {params};",
        f"    type Result = {result};",
        "}",
    ]
    types.add_type_info(request_def, get_message_type_name(request_def), lines)


def generate_partial_result(
    request_def: model.Request, types: TypeData, spec: model.LSPModel
) -> None:
    if not request_def.partialResult:
        return

    # Partial results are also typical covered in `model.Structures` that should already be generated
    # so we don't need to generate them here.


def generate_registration_options(
    request_def: model.Request, types: TypeData, spec: model.LSPModel
) -> None:
    if not request_def.registrationOptions:
        return

    # These types have references in `model.Structures` that should already be generated
    # so we don't need to generate them here.
