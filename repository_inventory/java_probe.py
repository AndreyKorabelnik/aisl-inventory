from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from source_syntax_primitives.java import (
    JavaAnnotationArgumentSyntax,
    annotation_syntax_from_node,
    child_by_field_name,
    java_declaration_syntax,
    java_parser_available,
    method_invocation_syntax_from_node,
    node_span,
    named_children,
    node_text as _text,
    object_creation_syntax_from_node,
    parse_java_source,
    walk_named_nodes as _walk,
)

from .canonical import fingerprint, stable_id
from .landscape import repository_local_salience, source_tree_scope


@dataclass
class JavaProbeResult:
    import_namespace_observations: list[dict[str, Any]]
    annotation_observations: list[dict[str, Any]]
    api_call_observations: list[dict[str, Any]]
    source_occurrences: list[dict[str, Any]]
    diagnostics: list[dict[str, Any]]
    probe_status: dict[str, dict[str, Any]]
    # Transient mechanics used only by repository-local transport half-wire projectors.
    # Both collections are produced from the same Tree-sitter parse pass.
    http_projection_facts: dict[str, list[dict[str, Any]]]
    kafka_projection_facts: dict[str, list[dict[str, Any]]]
    http_boundary_observations: list[dict[str, Any]]




_HTTP_METHODS=frozenset({'GET','POST','PUT','DELETE','PATCH','HEAD','OPTIONS','TRACE'})
_HTTP_ANNOTATION_METHODS={'Get':'GET','Post':'POST','Put':'PUT','Delete':'DELETE','Patch':'PATCH'}

def _literal_string(lexeme:str)->str|None:
    text=str(lexeme).strip()
    if len(text)>=2 and text[0]=='"' and text[-1]=='"' and '\\' not in text:
        return text[1:-1]
    return None

def _spring_property_key(lexeme:str)->str|None:
    value=_literal_string(lexeme)
    if value is None or not (value.startswith('${') and value.endswith('}')):
        return None
    body=value[2:-1].strip()
    if not body or ':' in body or '${' in body or '}' in body:
        return None
    return body

def _ancestor(node,types:set[str]):
    current=getattr(node,'parent',None)
    while current is not None:
        if str(getattr(current,'type','')) in types:
            return current
        current=getattr(current,'parent',None)
    return None

def _declaration_symbol(source:bytes,annotation_node)->tuple[str,str]|None:
    owner=_ancestor(annotation_node,{'field_declaration','formal_parameter'})
    if owner is None:
        return None
    if owner.type=='formal_parameter':
        name_node=child_by_field_name(owner,'name')
        name=_text(source,name_node).strip() if name_node is not None else ''
        return (name,'formal_parameter') if name else None
    declarator=next((child for child in named_children(owner) if child.type=='variable_declarator'),None)
    if declarator is None:
        return None
    name_node=child_by_field_name(declarator,'name')
    if name_node is None:
        name_node=next((child for child in named_children(declarator) if child.type=='identifier'),None)
    name=_text(source,name_node).strip() if name_node is not None else ''
    return (name,'field') if name else None

def _http_method_from_expression(expression:str)->str|None:
    token=str(expression).strip().rsplit('.',1)[-1]
    return token if token in _HTTP_METHODS else None

def _method_parameter_names(source:bytes,method_node)->list[str]:
    params=child_by_field_name(method_node,'parameters')
    if params is None:
        return []
    out=[]
    for child in named_children(params):
        if child.type not in {'formal_parameter','spread_parameter'}:
            continue
        name_node=child_by_field_name(child,'name')
        name=_text(source,name_node).strip() if name_node is not None else ''
        if name:
            out.append(name)
    return out


def _class_context(source:bytes,node)->tuple[str,list[str]]:
    owner=_ancestor(node,{"class_declaration"})
    if owner is None:
        return "",[]
    name_node=child_by_field_name(owner,"name")
    class_name=_text(source,name_node).strip() if name_node is not None else ""
    interfaces_node=child_by_field_name(owner,"interfaces")
    interfaces=[]
    if interfaces_node is not None:
        for nested in _walk(interfaces_node):
            if nested.type == "type_identifier":
                value=_text(source,nested).strip()
                if value and value not in interfaces:
                    interfaces.append(value)
    return class_name,interfaces


def _parameter_types(source:bytes,method_node)->dict[str,str]:
    params=child_by_field_name(method_node,"parameters")
    if params is None:
        return {}
    out={}
    for child in named_children(params):
        if child.type not in {"formal_parameter","spread_parameter"}:
            continue
        name_node=child_by_field_name(child,"name")
        type_node=child_by_field_name(child,"type")
        name=_text(source,name_node).strip() if name_node is not None else ""
        type_name=_text(source,type_node).strip() if type_node is not None else ""
        if name and type_name:
            out[name]=type_name.rsplit(".",1)[-1]
    return out


def _string_expression_fact(source:bytes,node,*,current_class:str)->dict[str,Any]|None:
    if node is None:
        return None
    node_type=str(getattr(node,"type",""))
    if node_type == "string_literal":
        value=_literal_string(_text(source,node))
        return {"kind":"literal","value":value} if value is not None else None
    if node_type == "identifier":
        name=_text(source,node).strip()
        return {"kind":"reference","owner":current_class,"name":name} if current_class and name else None
    if node_type == "field_access":
        object_node=child_by_field_name(node,"object")
        field_node=child_by_field_name(node,"field")
        owner=_text(source,object_node).strip() if object_node is not None else ""
        name=_text(source,field_node).strip() if field_node is not None else ""
        if owner and name and all(ch.isalnum() or ch in "_$" for ch in owner+name):
            return {"kind":"reference","owner":owner,"name":name}
        return None
    if node_type == "binary_expression":
        operator_node=child_by_field_name(node,"operator")
        if operator_node is None or _text(source,operator_node).strip() != "+":
            return None
        left=_string_expression_fact(source,child_by_field_name(node,"left"),current_class=current_class)
        right=_string_expression_fact(source,child_by_field_name(node,"right"),current_class=current_class)
        if left is None or right is None:
            return None
        return {"kind":"concat","parts":[left,right]}
    if node_type == "parenthesized_expression":
        children=named_children(node)
        if len(children) == 1:
            return _string_expression_fact(source,children[0],current_class=current_class)
    return None


def _static_final_string_constant(source:bytes,node)->tuple[str,dict[str,Any]]|None:
    if node.type != "field_declaration":
        return None
    type_node=child_by_field_name(node,"type")
    type_name=_text(source,type_node).strip() if type_node is not None else ""
    if type_name not in {"String","java.lang.String"}:
        return None
    modifiers=next((child for child in named_children(node) if child.type == "modifiers"),None)
    modifier_text=_text(source,modifiers) if modifiers is not None else ""
    if "static" not in modifier_text.split() or "final" not in modifier_text.split():
        return None
    declarator=next((child for child in named_children(node) if child.type == "variable_declarator"),None)
    if declarator is None:
        return None
    name_node=child_by_field_name(declarator,"name")
    value_node=child_by_field_name(declarator,"value")
    name=_text(source,name_node).strip() if name_node is not None else ""
    class_name,_=_class_context(source,node)
    expression=_string_expression_fact(source,value_node,current_class=class_name)
    if not class_name or not name or expression is None:
        return None
    return name,expression



def _simple_type_identity(type_text:str|None)->str|None:
    text=str(type_text or '').strip()
    if not text:
        return None
    # Keep generic shape, but normalize package-qualified outer/inner names only lexically.
    # This is not type resolution; it is a source-intrinsic declared-type observation.
    text=text.replace('java.lang.','').replace('java.util.','')
    return text

def _transport_payload_type(type_text:str|None)->str|None:
    text=_simple_type_identity(type_text)
    if not text:
        return None
    for wrapper in ('HttpEntity','RequestEntity','ResponseEntity','ParameterizedTypeReference'):
        prefix=wrapper+'<'
        if text.startswith(prefix) and text.endswith('>'):
            inner=text[len(prefix):-1].strip()
            return inner or None
    return text

def _method_symbol_types(source:bytes,method_node)->dict[str,str]:
    out=dict(_parameter_types(source,method_node))
    body=child_by_field_name(method_node,'body')
    if body is None:
        return out
    for nested in _walk(body):
        if nested.type!='local_variable_declaration':
            continue
        type_node=child_by_field_name(nested,'type')
        type_text=_text(source,type_node).strip() if type_node is not None else ''
        if not type_text:
            continue
        for child in named_children(nested):
            if child.type!='variable_declarator':
                continue
            name_node=child_by_field_name(child,'name')
            name=_text(source,name_node).strip() if name_node is not None else ''
            if name:
                out[name]=type_text
    return out

def _argument_declared_type(source:bytes,arg_node,method_node)->str|None:
    if arg_node is None:
        return None
    node_type=str(getattr(arg_node,'type',''))
    if node_type=='class_literal':
        name_node=child_by_field_name(arg_node,'name') or child_by_field_name(arg_node,'type')
        raw=_text(source,name_node).strip() if name_node is not None else _text(source,arg_node).strip()[:-6]
        return _simple_type_identity(raw)
    if node_type=='object_creation_expression':
        type_node=child_by_field_name(arg_node,'type')
        return _simple_type_identity(_text(source,type_node).strip() if type_node is not None else '')
    text=_text(source,arg_node).strip()
    if text.startswith('this.'):
        text=text[5:]
    if text and all(ch.isalnum() or ch in '_$' for ch in text):
        return _simple_type_identity(_method_symbol_types(source,method_node).get(text)) if method_node is not None else None
    return None

def _class_payload_declaration(source:bytes,node)->dict[str,Any]|None:
    if node.type!='class_declaration':
        return None
    name_node=child_by_field_name(node,'name')
    name=_text(source,name_node).strip() if name_node is not None else ''
    body=child_by_field_name(node,'body')
    if not name or body is None:
        return None
    fields=[]
    for field in named_children(body):
        if field.type!='field_declaration':
            continue
        modifiers=next((child for child in named_children(field) if child.type=='modifiers'),None)
        modifier_text=_text(source,modifiers) if modifiers is not None else ''
        if 'static' in modifier_text.split():
            continue
        type_node=child_by_field_name(field,'type')
        type_text=_text(source,type_node).strip() if type_node is not None else ''
        if not type_text:
            continue
        normalized=_simple_type_identity(type_text) or type_text
        collection='array' if (normalized.endswith('[]') or normalized.startswith(('List<','Set<','Collection<','Iterable<'))) else 'scalar'
        for declarator in named_children(field):
            if declarator.type!='variable_declarator':
                continue
            field_name_node=child_by_field_name(declarator,'name')
            field_name=_text(source,field_name_node).strip() if field_name_node is not None else ''
            if field_name:
                fields.append({'name':field_name,'declared_type':normalized,'collection':collection,'required':None})
    return {'type_name':name,'fields':fields}

def _manual_deserialize_types(source:bytes,method_node)->list[str]:
    body=child_by_field_name(method_node,'body') if method_node is not None else None
    if body is None:
        return []
    values=set()
    for nested in _walk(body):
        if nested.type!='method_invocation':
            continue
        syntax=method_invocation_syntax_from_node(source,nested)
        if syntax is None or syntax.method_name!='deserialize' or len(syntax.argument_nodes)<2:
            continue
        for arg in syntax.argument_nodes[1:]:
            if getattr(arg,'type','')!='class_literal':
                continue
            raw=_text(source,arg).strip()
            if raw.endswith('.class'):
                value=_simple_type_identity(raw[:-6])
                if value:
                    values.add(value)
    return sorted(values)


def _field_access_ref(source:bytes,node)->dict[str,str]|None:
    if node is None:
        return None
    node_type=str(getattr(node,'type',''))
    if node_type=='field_access':
        owner_node=child_by_field_name(node,'object')
        name_node=child_by_field_name(node,'field')
        owner=_text(source,owner_node).strip() if owner_node is not None else ''
        name=_text(source,name_node).strip() if name_node is not None else ''
        if owner and name:
            return {'owner':owner.rsplit('.',1)[-1],'name':name}
    if node_type=='identifier':
        name=_text(source,node).strip()
        return {'owner':'','name':name} if name else None
    return None


def _enum_constant_fact(source:bytes,node)->dict[str,Any]|None:
    if node.type!='enum_constant':
        return None
    owner=_ancestor(node,{'enum_declaration'})
    if owner is None:
        return None
    owner_name_node=child_by_field_name(owner,'name')
    owner_name=_text(source,owner_name_node).strip() if owner_name_node is not None else ''
    name_node=child_by_field_name(node,'name')
    if name_node is None:
        name_node=next((c for c in named_children(node) if c.type=='identifier'),None)
    name=_text(source,name_node).strip() if name_node is not None else ''
    args_node=next((c for c in named_children(node) if c.type=='argument_list'),None)
    args=list(named_children(args_node)) if args_node is not None else []
    if not owner_name or not name:
        return None
    return {
        'owner':owner_name,
        'name':name,
        'arguments':[_text(source,a).strip() for a in args],
        'argument_refs':[_field_access_ref(source,a) for a in args],
        'argument_literals':[_literal_string(_text(source,a).strip()) for a in args],
    }


def _class_super_enum_ref(source:bytes,node)->dict[str,str]|None:
    if node.type!='explicit_constructor_invocation':
        return None
    text=_text(source,node).lstrip()
    if not text.startswith('super'):
        return None
    args_node=next((c for c in named_children(node) if c.type=='argument_list'),None)
    args=list(named_children(args_node)) if args_node is not None else []
    if not args:
        return None
    return _field_access_ref(source,args[0])


def _method_return_type(source:bytes,method_node)->str|None:
    type_node=child_by_field_name(method_node,'type')
    if type_node is None:
        children=named_children(method_node)
        type_node=next((c for c in children if c.type in {'void_type','type_identifier','generic_type','integral_type','floating_point_type','boolean_type','array_type'}),None)
    value=_text(source,type_node).strip() if type_node is not None else ''
    return _simple_type_identity(value)


def _collection_element_type(type_text:str|None)->str|None:
    text=_simple_type_identity(type_text)
    if not text or '<' not in text or not text.endswith('>'):
        return None
    outer,inner=text.split('<',1)
    if outer.rsplit('.',1)[-1] not in {'List','Set','Collection','Iterable','Stream'}:
        return None
    inner=inner[:-1].strip()
    if not inner or ',' in inner:
        return None
    return _simple_type_identity(inner)


def _lambda_parameter_types(source:bytes,method_node,base_types:dict[str,str])->dict[str,str]:
    body=child_by_field_name(method_node,'body')
    if body is None:
        return {}
    out={}
    for nested in _walk(body):
        if nested.type!='method_invocation':
            continue
        syntax=method_invocation_syntax_from_node(source,nested)
        if syntax is None or syntax.method_name!='forEach' or syntax.receiver_node is None or len(syntax.argument_nodes)!=1:
            continue
        receiver=_text(source,syntax.receiver_node).strip()
        element_type=_collection_element_type(base_types.get(receiver))
        arg=syntax.argument_nodes[0]
        if element_type is None or getattr(arg,'type','')!='lambda_expression':
            continue
        params=child_by_field_name(arg,'parameters')
        if params is not None and getattr(params,'type','')=='identifier':
            name=_text(source,params).strip()
            if name:
                out[name]=element_type
        else:
            children=[c for c in named_children(arg) if c.type=='identifier']
            if len(children)==1:
                name=_text(source,children[0]).strip()
                if name:
                    out[name]=element_type
    return out


def _kafka_method_symbol_types(source:bytes,method_node)->dict[str,str]:
    out=_method_symbol_types(source,method_node)
    out.update(_lambda_parameter_types(source,method_node,out))
    return out


def _class_field_types(source:bytes,node)->dict[str,str]:
    owner=node if getattr(node,'type','')=='class_declaration' else _ancestor(node,{'class_declaration'})
    body=child_by_field_name(owner,'body') if owner is not None else None
    if body is None:
        return {}
    out={}
    for field in named_children(body):
        if field.type!='field_declaration':
            continue
        type_node=child_by_field_name(field,'type')
        type_text=_simple_type_identity(_text(source,type_node).strip() if type_node is not None else '')
        if not type_text:
            continue
        for declarator in named_children(field):
            if declarator.type!='variable_declarator':
                continue
            name_node=child_by_field_name(declarator,'name')
            name=_text(source,name_node).strip() if name_node is not None else ''
            if name:
                out[name]=type_text
    return out


def _class_method_return_types(source:bytes,node)->dict[str,list[str]]:
    owner=node if getattr(node,'type','')=='class_declaration' else _ancestor(node,{'class_declaration'})
    body=child_by_field_name(owner,'body') if owner is not None else None
    out=defaultdict(list)
    if body is None:
        return out
    for child in named_children(body):
        if child.type!='method_declaration':
            continue
        name_node=child_by_field_name(child,'name')
        name=_text(source,name_node).strip() if name_node is not None else ''
        result=_method_return_type(source,child)
        if name and result and result!='void':
            out[name].append(result)
    return out


def _expression_declared_type(source:bytes,node,method_node)->str|None:
    if node is None:
        return None
    direct=_argument_declared_type(source,node,method_node)
    if direct:
        return direct
    if getattr(node,'type','')=='identifier' and method_node is not None:
        symbol=_text(source,node).strip()
        value=_kafka_method_symbol_types(source,method_node).get(symbol)
        if value:
            return _simple_type_identity(value)
    if getattr(node,'type','')=='method_invocation':
        syntax=method_invocation_syntax_from_node(source,node)
        if syntax is not None and (syntax.receiver_node is None or _text(source,syntax.receiver_node).strip() in {'this',''}):
            returns=_class_method_return_types(source,node).get(syntax.method_name) or []
            unique=sorted(set(returns))
            if len(unique)==1:
                return unique[0]
    return None


def _local_config_ref(source:bytes,method_node,symbol:str)->dict[str,str]|None:
    body=child_by_field_name(method_node,'body') if method_node is not None else None
    if body is None:
        return None
    matches=[]
    for nested in _walk(body):
        if nested.type!='variable_declarator':
            continue
        name_node=child_by_field_name(nested,'name')
        if name_node is None or _text(source,name_node).strip()!=symbol:
            continue
        value_node=child_by_field_name(nested,'value')
        if value_node is None or value_node.type!='method_invocation':
            continue
        syntax=method_invocation_syntax_from_node(source,value_node)
        if syntax is None or syntax.method_name!='getStringValue' or len(syntax.argument_nodes)!=1:
            continue
        ref=_field_access_ref(source,syntax.argument_nodes[0])
        if ref:
            matches.append(ref)
    unique={(m['owner'],m['name']):m for m in matches}
    return next(iter(unique.values())) if len(unique)==1 else None


def _producer_record_binding(source:bytes,method_node,symbol:str)->tuple[Any,Any]|None:
    body=child_by_field_name(method_node,'body') if method_node is not None else None
    if body is None:
        return None
    matches=[]
    for nested in _walk(body):
        if nested.type!='variable_declarator':
            continue
        name_node=child_by_field_name(nested,'name')
        if name_node is None or _text(source,name_node).strip()!=symbol:
            continue
        value=child_by_field_name(nested,'value')
        if value is None or value.type!='object_creation_expression':
            continue
        syntax=object_creation_syntax_from_node(source,value)
        if syntax is None or syntax.syntactic_type.split('<',1)[0].rsplit('.',1)[-1]!='ProducerRecord':
            continue
        args=list(syntax.argument_nodes)
        if len(args)>=2:
            matches.append((args[0],args[-1]))
    return matches[0] if len(matches)==1 else None


def _kafka_template_send_context(source:bytes,invocation_node,method_node)->bool:
    syntax=method_invocation_syntax_from_node(source,invocation_node)
    if syntax is None or syntax.method_name!='send' or syntax.receiver_node is None:
        return False
    receiver=_text(source,syntax.receiver_node).strip()
    types=_kafka_method_symbol_types(source,method_node) if method_node is not None else {}
    types.update(_class_field_types(source,invocation_node))
    declared=types.get(receiver,'')
    if 'KafkaTemplate' in declared:
        return True
    # Exact transaction callback: kafkaTemplate.executeInTransaction(tpl -> ... tpl.send(...)).
    # The send may be nested in another lambda (for example eventList.forEach(event -> ...)),
    # so inspect every ancestor lambda until the method boundary rather than only the nearest.
    current=getattr(invocation_node,'parent',None)
    while current is not None and current is not method_node:
        if getattr(current,'type','')=='lambda_expression':
            params=child_by_field_name(current,'parameters')
            lambda_names=[]
            if params is not None and getattr(params,'type','')=='identifier':
                lambda_names=[_text(source,params).strip()]
            elif params is not None:
                lambda_names=[_text(source,c).strip() for c in named_children(params) if c.type=='identifier']
            if receiver in lambda_names:
                outer_node=getattr(current,'parent',None)
                while outer_node is not None and outer_node is not method_node:
                    if getattr(outer_node,'type','')=='method_invocation':
                        outer=method_invocation_syntax_from_node(source,outer_node)
                        if outer is not None and outer.method_name=='executeInTransaction' and outer.receiver_node is not None:
                            outer_receiver=_text(source,outer.receiver_node).strip()
                            return 'KafkaTemplate' in types.get(outer_receiver,'')
                    # Stop at the next enclosing lambda; an executeInTransaction invocation
                    # owning this lambda must be between the lambda and that outer lambda.
                    if getattr(outer_node,'type','')=='lambda_expression':
                        break
                    outer_node=getattr(outer_node,'parent',None)
        current=getattr(current,'parent',None)
    return False


def _kafka_publish_candidate(source:bytes,node)->dict[str,Any]|None:
    syntax=method_invocation_syntax_from_node(source,node)
    if syntax is None or syntax.method_name!='send':
        return None
    method_node=_ancestor(node,{'method_declaration'})
    if method_node is None or not _kafka_template_send_context(source,node,method_node):
        return None
    args=list(syntax.argument_nodes)
    if not args:
        return None
    topic_node=None
    payload_node=None
    if len(args)==1:
        symbol=_text(source,args[0]).strip()
        if symbol.startswith('this.'):
            symbol=symbol[5:]
        if not symbol or not all(ch.isalnum() or ch in '_$' for ch in symbol):
            return None
        binding=_producer_record_binding(source,method_node,symbol)
        if binding is None:
            return None
        topic_node,payload_node=binding
    elif len(args)==2:
        topic_node,payload_node=args[0],args[1]
    elif len(args)==3:
        topic_node,payload_node=args[0],args[2]
    else:
        topic_node,payload_node=args[0],args[-1]
    topic_expression=_text(source,topic_node).strip()
    topic_ref=None
    topic_literal=_literal_string(topic_expression)
    if topic_literal is None:
        symbol=topic_expression[5:] if topic_expression.startswith('this.') else topic_expression
        if symbol and all(ch.isalnum() or ch in '_$' for ch in symbol):
            topic_ref=_local_config_ref(source,method_node,symbol)
    payload_type=_expression_declared_type(source,payload_node,method_node)
    class_name,_=_class_context(source,node)
    method_name_node=child_by_field_name(method_node,'name')
    method_name=_text(source,method_name_node).strip() if method_name_node is not None else ''
    return {
        'class_name':class_name,
        'method_name':method_name,
        'topic_expression':topic_expression,
        'topic_literal':topic_literal,
        'topic_config_ref':topic_ref,
        'payload_type':_transport_payload_type(payload_type),
    }


def _enum_transport_metadata(source:bytes,node)->dict[str,Any]|None:
    if getattr(node,'type','')!='enum_declaration':
        return None
    name_node=child_by_field_name(node,'name')
    owner=_text(source,name_node).strip() if name_node is not None else ''
    if not owner:
        return None
    modifiers=next((c for c in named_children(node) if c.type=='modifiers'),None)
    modifier_text=_text(source,modifiers) if modifiers is not None else ''
    required_args='RequiredArgsConstructor' in modifier_text
    fields=[]
    accessors=[]
    for nested in _walk(node):
        if nested.type=='field_declaration':
            mods=next((c for c in named_children(nested) if c.type=='modifiers'),None)
            mods_text=_text(source,mods) if mods is not None else ''
            tokens=mods_text.replace('\n',' ').split()
            if 'final' not in tokens or 'static' in tokens:
                continue
            type_node=child_by_field_name(nested,'type')
            type_text=_simple_type_identity(_text(source,type_node).strip() if type_node is not None else '')
            for declarator in named_children(nested):
                if declarator.type!='variable_declarator':
                    continue
                field_name_node=child_by_field_name(declarator,'name')
                field_name=_text(source,field_name_node).strip() if field_name_node is not None else ''
                if field_name:
                    fields.append({'name':field_name,'declared_type':type_text,'start_byte':int(getattr(nested,'start_byte',0))})
        elif nested.type=='method_declaration':
            method_name_node=child_by_field_name(nested,'name')
            method_name=_text(source,method_name_node).strip() if method_name_node is not None else ''
            body=child_by_field_name(nested,'body')
            if not method_name or body is None:
                continue
            refs=[]
            for call in _walk(body):
                if call.type!='method_invocation':
                    continue
                syntax=method_invocation_syntax_from_node(source,call)
                if syntax is None or syntax.method_name!='getStringValue' or len(syntax.argument_nodes)!=1:
                    continue
                arg=syntax.argument_nodes[0]
                if getattr(arg,'type','')=='identifier':
                    value=_text(source,arg).strip()
                    if value:
                        refs.append(value)
            unique=sorted(set(refs))
            if len(unique)==1:
                accessors.append({'method_name':method_name,'field_name':unique[0]})
    fields=sorted(fields,key=lambda row:row['start_byte'])
    for row in fields:
        row.pop('start_byte',None)
    return {'owner':owner,'required_args_constructor':required_args,'required_fields':fields,'string_accessors':accessors}


def _annotation_named_literal(syntax,name:str)->str|None:
    for argument in syntax.arguments:
        if argument.name!=name:
            continue
        return _literal_string(argument.raw_value)
    return None

def _runtime_source_path(path:str)->bool:
    normalized='/' + str(path).replace('\\','/').lstrip('/')
    return '/src/test/' not in normalized and '/src/it/' not in normalized and '/test/' not in normalized


def _simple_symbol(expression: str) -> str | None:
    text=str(expression or "").strip()
    if text.startswith("this."):
        text=text[5:]
    if not text or not (text[0].isalpha() or text[0] in "_$"):
        return None
    if not all(ch.isalnum() or ch in "_$" for ch in text):
        return None
    return text


def _method_chain(source: bytes, node) -> tuple[str | None, list[tuple[str, tuple[Any, ...], Any]]] | None:
    """Return the lexical receiver root and receiver-chain method invocations.

    This consumes Tree-sitter-owned method-invocation nodes only. Nested argument
    calls are intentionally excluded; callers can inspect argument subtrees
    separately when they need serializer/deserializer evidence.
    """
    if getattr(node, "type", "") != "method_invocation":
        return None
    calls: list[tuple[str, tuple[Any, ...], Any]] = []
    current=node
    root: str | None = None
    while getattr(current, "type", "") == "method_invocation":
        syntax=method_invocation_syntax_from_node(source,current)
        if syntax is None:
            return None
        calls.append((syntax.method_name, tuple(syntax.argument_nodes), current))
        receiver=syntax.receiver_node
        if receiver is None:
            root=None
            break
        if getattr(receiver, "type", "") == "method_invocation":
            current=receiver
            continue
        root=_text(source,receiver).strip()
        break
    calls.reverse()
    return root,calls


def _variable_declarator_type(source: bytes, node) -> str | None:
    parent=getattr(node,"parent",None)
    if parent is None or getattr(parent,"type","") != "local_variable_declaration":
        return None
    type_node=child_by_field_name(parent,"type")
    if type_node is None:
        named=list(named_children(parent))
        type_node=named[0] if named and named[0] is not node else None
    return _text(source,type_node).strip() if type_node is not None else None


def _class_exact_bindings(source: bytes, class_node) -> dict[str, list[Any]]:
    """Collect only exact simple-symbol assignments inside one class.

    Multiple assignments are preserved and therefore become unresolved during
    recursive tracing; this helper never chooses one assignment heuristically.
    """
    out: dict[str, list[Any]] = defaultdict(list)
    for nested in _walk(class_node):
        if getattr(nested,"type","") == "variable_declarator":
            name_node=child_by_field_name(nested,"name")
            value_node=child_by_field_name(nested,"value")
            symbol=_simple_symbol(_text(source,name_node).strip()) if name_node is not None else None
            if symbol and value_node is not None:
                out[symbol].append(value_node)
        elif getattr(nested,"type","") == "assignment_expression":
            left=child_by_field_name(nested,"left")
            right=child_by_field_name(nested,"right")
            symbol=_simple_symbol(_text(source,left).strip()) if left is not None else None
            if symbol and right is not None:
                out[symbol].append(right)
    return out


def _getter_property_suffix(
    source: bytes,
    node,
    bindings: dict[str, list[Any]],
    *,
    visiting: frozenset[str] = frozenset(),
) -> tuple[str, tuple[str, ...]] | None:
    """Trace an exact getter chain to ``rootSymbol + property segments``.

    This is deliberately bounded to repository-local aliases, ``URI.create(x)``
    and zero-argument JavaBean getters. The result is only a config-key suffix
    candidate; it does not claim that the root symbol is a configuration object.
    """
    node_type=getattr(node,"type","")
    if node_type in {"identifier","field_access"}:
        text=_text(source,node).strip()
        symbol=_simple_symbol(text)
        if symbol is None:
            return None
        rows=bindings.get(symbol) or []
        if len(rows)==1 and symbol not in visiting:
            expanded=_getter_property_suffix(source,rows[0],bindings,visiting=visiting|{symbol})
            if expanded is not None:
                return expanded
        return symbol,()
    if node_type != "method_invocation":
        return None
    syntax=method_invocation_syntax_from_node(source,node)
    if syntax is None:
        return None
    if syntax.method_name == "create" and len(syntax.argument_nodes)==1:
        receiver=_text(source,syntax.receiver_node).strip() if syntax.receiver_node is not None else ""
        if receiver.rsplit(".",1)[-1] == "URI":
            return _getter_property_suffix(source,syntax.argument_nodes[0],bindings,visiting=visiting)
    if syntax.argument_nodes or not syntax.method_name.startswith("get") or len(syntax.method_name) <= 3 or syntax.receiver_node is None:
        return None
    base=_getter_property_suffix(source,syntax.receiver_node,bindings,visiting=visiting)
    if base is None:
        return None
    prop=syntax.method_name[3:]
    prop=prop[:1].lower()+prop[1:]
    return base[0],base[1]+(prop,)


def _nested_serializer_payload_type(source: bytes, node, method_node) -> str | None:
    candidates: set[str] = set()
    for nested in _walk(node):
        if getattr(nested,"type","") != "method_invocation":
            continue
        syntax=method_invocation_syntax_from_node(source,nested)
        if syntax is None or syntax.method_name != "writeValueAsString" or len(syntax.argument_nodes)!=1:
            continue
        declared=_argument_declared_type(source,syntax.argument_nodes[0],method_node)
        identity=_transport_payload_type(declared)
        if identity:
            candidates.add(identity)
    return next(iter(candidates)) if len(candidates)==1 else None


def _response_payload_for_symbol(source: bytes, method_node, response_symbol: str) -> str | None:
    body=child_by_field_name(method_node,"body")
    if body is None:
        return None
    candidates: set[str] = set()
    for nested in _walk(body):
        if getattr(nested,"type","") != "method_invocation":
            continue
        syntax=method_invocation_syntax_from_node(source,nested)
        if syntax is None or syntax.method_name != "readValue" or len(syntax.argument_nodes)<2:
            continue
        first=syntax.argument_nodes[0]
        first_syntax=method_invocation_syntax_from_node(source,first) if getattr(first,"type","")=="method_invocation" else None
        if first_syntax is None or first_syntax.method_name != "body" or first_syntax.receiver_node is None:
            continue
        if _simple_symbol(_text(source,first_syntax.receiver_node).strip()) != response_symbol:
            continue
        type_arg=syntax.argument_nodes[1]
        if getattr(type_arg,"type","") == "class_literal":
            raw=_text(source,type_arg).strip()
            if raw.endswith(".class"):
                identity=_simple_type_identity(raw[:-6])
                if identity:
                    candidates.add(identity)
    return next(iter(candidates)) if len(candidates)==1 else None


def _jdk_http_calls_in_method(source: bytes, method_node) -> list[tuple[dict[str, Any], Any]]:
    """Observe bounded ``java.net.http.HttpClient`` builder/send calls in one method.

    A boundary is emitted only when the same method contains both an exact
    ``HttpRequest.newBuilder()`` variable and ``send(request.build(), ...)``.
    This is semantic projection over Tree-sitter structure, not another parser.
    """
    body=child_by_field_name(method_node,"body")
    if body is None:
        return []
    class_node=_ancestor(method_node,{"class_declaration"})
    bindings=_class_exact_bindings(source,class_node) if class_node is not None else {}
    builders: dict[str, dict[str, Any]] = {}
    for nested in _walk(body):
        if getattr(nested,"type","") != "variable_declarator":
            continue
        declared_type=_simple_type_identity(_variable_declarator_type(source,nested))
        if declared_type not in {"HttpRequest.Builder","java.net.http.HttpRequest.Builder"}:
            continue
        name_node=child_by_field_name(nested,"name")
        value_node=child_by_field_name(nested,"value")
        symbol=_simple_symbol(_text(source,name_node).strip()) if name_node is not None else None
        chain=_method_chain(source,value_node) if value_node is not None else None
        if symbol is None or chain is None:
            continue
        root,calls=chain
        if str(root or "").rsplit(".",1)[-1] != "HttpRequest" or not calls or calls[0][0] != "newBuilder":
            continue
        method_calls=[row for row in calls if row[0] in _HTTP_METHODS]
        uri_calls=[row for row in calls if row[0] == "uri" and len(row[1])==1]
        if len(method_calls)!=1 or len(uri_calls)!=1:
            continue
        method_name,method_args,_=method_calls[0]
        uri_arg=uri_calls[0][1][0]
        request_payload=None
        if method_args:
            request_payload=_nested_serializer_payload_type(source,method_args[0],method_node)
        suffix=_getter_property_suffix(source,uri_arg,bindings)
        builders[symbol]={
            "request_symbol":symbol,
            "method":method_name,
            "uri_expression":_text(source,uri_arg).strip(),
            "config_key_suffix":".".join(suffix[1]) if suffix is not None and suffix[1] else None,
            "config_root_symbol":suffix[0] if suffix is not None and suffix[1] else None,
            "request_payload":request_payload,
            "builder_node":nested,
        }
    if not builders:
        return []

    out: list[tuple[dict[str, Any], Any]] = []
    for nested in _walk(body):
        if getattr(nested,"type","") != "method_invocation":
            continue
        syntax=method_invocation_syntax_from_node(source,nested)
        if syntax is None or syntax.method_name != "send" or not syntax.argument_nodes:
            continue
        first=syntax.argument_nodes[0]
        build_syntax=method_invocation_syntax_from_node(source,first) if getattr(first,"type","")=="method_invocation" else None
        if build_syntax is None or build_syntax.method_name != "build" or build_syntax.receiver_node is None:
            continue
        request_symbol=_simple_symbol(_text(source,build_syntax.receiver_node).strip())
        if request_symbol not in builders:
            continue
        response_symbol=None
        parent=getattr(nested,"parent",None)
        if parent is not None and getattr(parent,"type","") == "variable_declarator" and child_by_field_name(parent,"value") == nested:
            name_node=child_by_field_name(parent,"name")
            response_symbol=_simple_symbol(_text(source,name_node).strip()) if name_node is not None else None
        fact=dict(builders[request_symbol])
        fact.pop("builder_node",None)
        fact["response_symbol"]=response_symbol
        fact["response_payload"]=_response_payload_for_symbol(source,method_node,response_symbol) if response_symbol else None
        out.append((fact,nested))
    return out

def _occurrence(*, repository_id: str, path: str, sha: str | None, node, basis: str) -> dict[str, Any]:
    span = node_span(node)
    line_start = span.line_start
    line_end = span.line_end
    col_start = span.column_start
    col_end = span.column_end
    oid = stable_id("source_occurrence", repository_id, path, "exact_span", line_start, line_end, col_start, col_end, basis, sha)
    return {
        "occurrence_id": oid,
        "repository_id": repository_id,
        "repository_relative_path": path,
        "localization_kind": "exact_span",
        "line_start": line_start,
        "line_end": line_end,
        "column_start": col_start,
        "column_end": col_end,
        "content_sha256": sha,
        "provenance": {"probe_id": basis, "probe_version": "2", "basis": "tree_sitter_java_ast"},
    }


def _diag(*, repository_id: str, path: str, source_occurrence_id: str | None, code: str, message: str, basis: dict[str, Any]) -> dict[str, Any]:
    return {
        "diagnostic_id": stable_id("inventory_diagnostic", repository_id, path, "java", code, basis),
        "code": code,
        "severity": "warning",
        "message": message,
        "source_ref": {"repository_relative_path": path},
        "source_occurrence_id": source_occurrence_id,
        "probe": {"probe_id": "java_structural_observations", "probe_version": "2"},
        "basis": basis,
    }


def _literal_value(argument: JavaAnnotationArgumentSyntax) -> dict[str, Any] | None:
    raw = argument.raw_value
    node_type = argument.value_node_type
    if node_type == "string_literal":
        return {"value_kind": "literal", "observed_value": {"literal_kind": "string", "lexeme": raw}}
    if node_type == "character_literal":
        return {"value_kind": "literal", "observed_value": {"literal_kind": "char", "lexeme": raw}}
    if node_type in {"decimal_integer_literal", "hex_integer_literal", "octal_integer_literal", "binary_integer_literal", "decimal_floating_point_literal", "hex_floating_point_literal"}:
        return {"value_kind": "literal", "observed_value": {"literal_kind": "number", "lexeme": raw}}
    if node_type in {"true", "false"}:
        return {"value_kind": "literal", "observed_value": {"literal_kind": "boolean", "lexeme": raw}}
    if node_type == "null_literal":
        return {"value_kind": "literal", "observed_value": {"literal_kind": "null", "lexeme": raw}}
    if node_type == "class_literal":
        name = raw[:-6] if raw.endswith(".class") else raw
        return {"value_kind": "class_literal", "observed_symbol": name}
    return None


def _annotation_arg(argument: JavaAnnotationArgumentSyntax) -> dict[str, Any]:
    value = _literal_value(argument)
    out: dict[str, Any] = {"position": argument.position, "name": argument.name}
    if value is not None:
        out.update(value)
        return out
    kind_map = {
        "element_value_array_initializer": "array_initializer_expression",
        "annotation": "nested_annotation_expression",
        "marker_annotation": "nested_annotation_expression",
    }
    out["value_kind"] = kind_map.get(argument.value_node_type, "expression")
    out["expression_fingerprint"] = fingerprint({"node_type": argument.value_node_type, "text": argument.raw_value})
    return out


def run_java_probes(*, repository_id: str, files: list[dict[str, Any]], source_bytes_by_path: dict[str, bytes], file_occurrence_by_path: dict[str, str], max_probe_file_bytes: int) -> JavaProbeResult:
    imports: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    occurrences: dict[str, dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []
    property_bindings: list[dict[str, Any]] = []
    http_invocations: list[dict[str, Any]] = []
    wrapper_summaries: list[dict[str, Any]] = []
    jdk_http_calls: list[dict[str, Any]] = []
    string_constants: list[dict[str, Any]] = []
    route_annotations: list[dict[str, Any]] = []
    service_registrations: list[dict[str, Any]] = []
    type_declarations: list[dict[str, Any]] = []
    http_boundary_observations: list[dict[str, Any]] = []
    enum_constants: list[dict[str, Any]] = []
    enum_metadata: list[dict[str, Any]] = []
    constructor_enum_refs: list[dict[str, Any]] = []
    kafka_listeners: list[dict[str, Any]] = []
    kafka_publish_candidates: list[dict[str, Any]] = []
    java_files = [row for row in files if row.get("extension") == ".java" and row.get("readable")]
    skipped = failed = 0
    parser_ok, parser_detail = java_parser_available()
    if not parser_ok:
        diagnostics.append(_diag(repository_id=repository_id, path="", source_occurrence_id=None, code="java_parser_unavailable", message="Tree-sitter Java parser is unavailable; no Java observations were emitted.", basis={"error": parser_detail}))
        status = "failed" if java_files else "not_applicable"
        ps = {k: {"status": status, "basis": {"kind": "tree_sitter_java_unavailable"}} for k in ("import_namespace_observations", "annotation_observations", "api_call_observations")}
        return JavaProbeResult([], [], [], [], diagnostics, ps, {'property_bindings': [], 'invocations': [], 'wrapper_summaries': [], 'jdk_http_calls': [], 'string_constants': [], 'route_annotations': [], 'service_registrations': [], 'type_declarations': []}, {'enum_constants': [], 'enum_metadata': [], 'constructor_enum_refs': [], 'listeners': [], 'publish_candidates': [], 'type_declarations': []}, [])

    file_by_path = {r["repository_relative_path"]: r for r in files}
    for file_row in java_files:
        path = file_row["repository_relative_path"]
        raw = source_bytes_by_path.get(path)
        if raw is None:
            continue
        if len(raw) > max_probe_file_bytes:
            skipped += 1
            diagnostics.append(_diag(repository_id=repository_id, path=path, source_occurrence_id=file_occurrence_by_path.get(path), code="java_probe_file_too_large", message="Java structural probe skipped an oversized file.", basis={"byte_size": len(raw), "max_probe_file_bytes": max_probe_file_bytes}))
            continue
        try:
            raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            failed += 1
            diagnostics.append(_diag(repository_id=repository_id, path=path, source_occurrence_id=file_occurrence_by_path.get(path), code="java_utf8_decode_failed", message="Java source could not be decoded as UTF-8.", basis={"reason": exc.reason}))
            continue
        # Parser construction, invocation, traversal and point normalization are owned by
        # source-syntax-primitives. Inventory keeps file-local parser lifetime semantics
        # through parse_java_source's default fresh-parser path.
        parsed_source = parse_java_source(raw)
        if parsed_source.has_error:
            failed += 1
            diagnostics.append(_diag(repository_id=repository_id, path=path, source_occurrence_id=file_occurrence_by_path.get(path), code="java_tree_sitter_parse_error", message="Tree-sitter Java reported syntax recovery/errors; observations from this file are withheld.", basis={"root_type": parsed_source.root.type}))
            continue

        declarations = java_declaration_syntax(raw, parsed_source.root)
        if declarations.package is not None:
            item = declarations.package
            name = item.name
            occ = _occurrence(repository_id=repository_id, path=path, sha=file_row.get("sha256"), node=item.syntax_node, basis="java_package_declaration")
            occurrences[occ["occurrence_id"]] = occ
            imports.append({"import_namespace_observation_id":stable_id("import_namespace_observation",repository_id,path,"package",name,item.span.line_start),"repository_id":repository_id,"file_id":file_row["file_id"],"repository_relative_path":path,"source_occurrence_id":occ["occurrence_id"],"language":"java","observation_kind":"package_declaration","namespace":name,"is_static":False,"is_wildcard":False,"claim":{"classification":"observed_fact","confidence":1.0,"basis":"tree_sitter_java_package_declaration"},"probe":{"probe_id":"import_namespace_observations","probe_version":"2"}})
        for item in declarations.imports:
            name = item.name + (".*" if item.is_wildcard else "")
            occ = _occurrence(repository_id=repository_id, path=path, sha=file_row.get("sha256"), node=item.syntax_node, basis="java_import_declaration")
            occurrences[occ["occurrence_id"]] = occ
            imports.append({"import_namespace_observation_id":stable_id("import_namespace_observation",repository_id,path,"import",name,item.is_static,item.span.line_start),"repository_id":repository_id,"file_id":file_row["file_id"],"repository_relative_path":path,"source_occurrence_id":occ["occurrence_id"],"language":"java","observation_kind":"import_declaration","namespace":name,"is_static":item.is_static,"is_wildcard":item.is_wildcard,"claim":{"classification":"observed_fact","confidence":1.0,"basis":"tree_sitter_java_import_declaration"},"probe":{"probe_id":"import_namespace_observations","probe_version":"2"}})

        for node in _walk(parsed_source.root):
            if _runtime_source_path(path) and node.type == "enum_declaration":
                meta=_enum_transport_metadata(raw,node)
                if meta is not None:
                    meta.update({"repository_relative_path":path})
                    enum_metadata.append(meta)
            if _runtime_source_path(path) and node.type == "enum_constant":
                fact=_enum_constant_fact(raw,node)
                if fact is not None:
                    occ=_occurrence(repository_id=repository_id,path=path,sha=file_row.get("sha256"),node=node,basis="java_enum_constant")
                    occurrences[occ["occurrence_id"]]=occ
                    fact.update({"repository_relative_path":path,"source_occurrence_id":occ["occurrence_id"]})
                    enum_constants.append(fact)
            if _runtime_source_path(path) and node.type == "explicit_constructor_invocation":
                ref=_class_super_enum_ref(raw,node)
                if ref is not None:
                    class_name,_=_class_context(raw,node)
                    if class_name:
                        occ=_occurrence(repository_id=repository_id,path=path,sha=file_row.get("sha256"),node=node,basis="java_super_constructor_enum_ref")
                        occurrences[occ["occurrence_id"]]=occ
                        constructor_enum_refs.append({"class_name":class_name,"enum_ref":ref,"repository_relative_path":path,"source_occurrence_id":occ["occurrence_id"]})
            if node.type == "class_declaration" and _runtime_source_path(path):
                declaration=_class_payload_declaration(raw,node)
                if declaration is not None:
                    declaration.update({"repository_relative_path":path})
                    type_declarations.append(declaration)
            if node.type == "field_declaration" and _runtime_source_path(path):
                constant=_static_final_string_constant(raw,node)
                if constant is not None:
                    constant_name,expression=constant
                    class_name,_=_class_context(raw,node)
                    occ=_occurrence(repository_id=repository_id,path=path,sha=file_row.get("sha256"),node=node,basis="java_exact_string_constant")
                    occurrences[occ["occurrence_id"]]=occ
                    string_constants.append({"owner":class_name,"name":constant_name,"expression":expression,"repository_relative_path":path,"source_occurrence_id":occ["occurrence_id"]})
            if node.type == "method_declaration" and _runtime_source_path(path):
                method_name_node=child_by_field_name(node,"name")
                method_name=_text(raw,method_name_node).strip() if method_name_node is not None else ""
                parameters=_method_parameter_names(raw,node)
                body=child_by_field_name(node,"body")
                if method_name and body is not None:
                    for nested in _walk(body):
                        if nested.type != "method_invocation":
                            continue
                        nested_syntax=method_invocation_syntax_from_node(raw,nested)
                        if nested_syntax is None or nested_syntax.method_name != "exchange" or len(nested_syntax.argument_nodes) < 2:
                            continue
                        path_expr=_text(raw,nested_syntax.argument_nodes[0]).strip()
                        method_expr=_text(raw,nested_syntax.argument_nodes[1]).strip()
                        method=_http_method_from_expression(method_expr)
                        if method is None or path_expr not in parameters:
                            continue
                        occ=_occurrence(repository_id=repository_id,path=path,sha=file_row.get("sha256"),node=node,basis="java_http_wrapper_method")
                        occurrences[occ["occurrence_id"]]=occ
                        
                        symbol_types=_method_symbol_types(raw,node)
                        exchange_args=[_text(raw,arg).strip() for arg in nested_syntax.argument_nodes]
                        body_parameter_index=None
                        response_parameter_index=None
                        fixed_response_payload=None
                        if len(exchange_args)>=3:
                            entity_symbol=exchange_args[2]
                            if entity_symbol in parameters:
                                body_parameter_index=parameters.index(entity_symbol)
                            else:
                                # Bounded method-local bridge: HttpEntity<X> local initialized from a
                                # wrapper parameter (direct constructor or one direct helper call).
                                for local in _walk(body):
                                    if local.type!='variable_declarator':
                                        continue
                                    name_node=child_by_field_name(local,'name')
                                    if name_node is None or _text(raw,name_node).strip()!=entity_symbol:
                                        continue
                                    value_node=child_by_field_name(local,'value')
                                    if value_node is None:
                                        continue
                                    candidates=[]
                                    if value_node.type=='object_creation_expression':
                                        args_node=child_by_field_name(value_node,'arguments')
                                        candidates=list(named_children(args_node)) if args_node is not None else []
                                    elif value_node.type=='method_invocation':
                                        value_syntax=method_invocation_syntax_from_node(raw,value_node)
                                        candidates=list(value_syntax.argument_nodes) if value_syntax is not None else []
                                    for candidate in candidates[:1]:
                                        symbol=_text(raw,candidate).strip()
                                        if symbol in parameters:
                                            body_parameter_index=parameters.index(symbol)
                                    break
                        if len(exchange_args)>=4:
                            response_expr=exchange_args[3]
                            if response_expr in parameters:
                                response_parameter_index=parameters.index(response_expr)
                            elif response_expr.endswith('.class'):
                                fixed_response_payload=_simple_type_identity(response_expr[:-6])
                        wrapper_summaries.append({"callable_name":method_name,"argument_count":len(parameters),"path_parameter_index":parameters.index(path_expr),"path_parameter_name":path_expr,"body_parameter_index":body_parameter_index,"response_parameter_index":response_parameter_index,"fixed_response_payload":fixed_response_payload,"method":method,"repository_relative_path":path,"source_occurrence_id":occ["occurrence_id"]})
                for jdk_fact,jdk_node in _jdk_http_calls_in_method(raw,node):
                    occ=_occurrence(repository_id=repository_id,path=path,sha=file_row.get("sha256"),node=jdk_node,basis="java_jdk_http_client_send")
                    occurrences[occ["occurrence_id"]]=occ
                    jdk_fact.update({"repository_relative_path":path,"source_occurrence_id":occ["occurrence_id"]})
                    jdk_http_calls.append(jdk_fact)
            if node.type in {"annotation","marker_annotation"}:
                syntax = annotation_syntax_from_node(raw, node)
                if syntax is not None:
                    name=syntax.name; args=[_annotation_arg(argument) for argument in syntax.arguments]; occ=_occurrence(repository_id=repository_id,path=path,sha=file_row.get("sha256"),node=syntax.syntax_node,basis="java_annotation_usage"); occurrences[occ["occurrence_id"]]=occ
                    annotations.append({"annotation_observation_id":stable_id("annotation_observation",repository_id,path,name,syntax.span.line_start,syntax.span.column_start),"repository_id":repository_id,"file_id":file_row["file_id"],"repository_relative_path":path,"source_occurrence_id":occ["occurrence_id"],"language":"java","annotation_name":name,"simple_name":name.rsplit(".",1)[-1],"argument_count":len(args),"arguments":args,"parse_status":"complete","claim":{"classification":"observed_fact","confidence":1.0,"basis":"tree_sitter_java_annotation"},"probe":{"probe_id":"annotation_observations","probe_version":"2"}})
                    simple_name=name.rsplit(".",1)[-1]
                    if simple_name == "KafkaListener" and _runtime_source_path(path):
                        method_node=_ancestor(syntax.syntax_node,{"method_declaration"})
                        class_name,_=_class_context(raw,syntax.syntax_node)
                        if method_node is not None and class_name:
                            method_name_node=child_by_field_name(method_node,"name")
                            method_name=_text(raw,method_name_node).strip() if method_name_node is not None else ""
                            topics_literal=_annotation_named_literal(syntax,"topics")
                            kafka_listeners.append({"class_name":class_name,"method_name":method_name,"topics_literal":topics_literal,"payload_candidates":_manual_deserialize_types(raw,method_node),"repository_relative_path":path,"source_occurrence_id":occ["occurrence_id"]})
                    if simple_name == "Value" and syntax.arguments:
                        property_key=_spring_property_key(syntax.arguments[0].raw_value)
                        symbol=_declaration_symbol(raw,syntax.syntax_node)
                        if property_key and symbol and _runtime_source_path(path):
                            property_bindings.append({"symbol":symbol[0],"binding_kind":symbol[1],"property_key":property_key,"repository_relative_path":path,"source_occurrence_id":occ["occurrence_id"]})
                    http_method=_HTTP_ANNOTATION_METHODS.get(simple_name)
                    if http_method and syntax.arguments and _ancestor(syntax.syntax_node,{"method_declaration"}) is not None and _runtime_source_path(path):
                        class_name,interfaces=_class_context(raw,syntax.syntax_node)
                        route_expression=_string_expression_fact(raw,syntax.arguments[0].value_node,current_class=class_name)
                        if route_expression is not None and class_name:
                            route_annotations.append({"class_name":class_name,"interfaces":interfaces,"method":http_method,"expression":route_expression,"manual_deserialize_types":_manual_deserialize_types(raw,_ancestor(syntax.syntax_node,{"method_declaration"})),"repository_relative_path":path,"source_occurrence_id":occ["occurrence_id"]})
                        route=_literal_string(syntax.arguments[0].raw_value)
                        if route is not None:
                            http_boundary_observations.append({"family_id":stable_id("http_boundary_observation",repository_id,"java_annotation",path,http_method,route,syntax.span.line_start),"repository_id":repository_id,"family_kind":"http_boundary_observation","syntax_family":"java","key":route,"direction":"inbound","protocol":"http","method":http_method,"path":route,"path_status":"resolved","source_kind":"java_literal_route_annotation","repository_relative_path":path,"source_occurrence_id":occ["occurrence_id"],"occurrence_count":1,"claim":{"classification":"observed_fact","confidence":1.0,"basis":"tree_sitter_java_literal_route_annotation"},"probe":{"probe_id":"http_boundary_observations","probe_version":"1"},"basis":{"kind":"tree_sitter_java_literal_route_annotation","semantic_meaning_inferred":False}})
            elif node.type == "method_invocation":
                syntax = method_invocation_syntax_from_node(raw, node)
                if syntax is None: continue
                basis = syntax.receiver_shape
                receiver = _text(raw, syntax.receiver_node).strip() if basis == "lexical_receiver" else None
                occ=None
                if basis != "unqualified":
                    occ=_occurrence(repository_id=repository_id,path=path,sha=file_row.get("sha256"),node=syntax.syntax_node,basis="java_qualified_method_invocation"); occurrences[occ["occurrence_id"]]=occ
                    events.append({"repository_relative_path":path,"source_occurrence_id":occ["occurrence_id"],"call_kind":"qualified_method_invocation","callable_name":syntax.method_name,"receiver_identity":receiver,"receiver_basis":basis,"argument_count":syntax.argument_count,"source_tree_scope":source_tree_scope(path)})
                if _runtime_source_path(path):
                    if occ is None:
                        occ=_occurrence(repository_id=repository_id,path=path,sha=file_row.get("sha256"),node=syntax.syntax_node,basis="java_http_projection_invocation"); occurrences[occ["occurrence_id"]]=occ
                    method_node=_ancestor(syntax.syntax_node,{"method_declaration"})
                    http_invocations.append({"callable_name":syntax.method_name,"receiver_identity":receiver,"receiver_basis":basis,"argument_count":syntax.argument_count,"arguments":[_text(raw,arg).strip() for arg in syntax.argument_nodes],"argument_declared_types":[_argument_declared_type(raw,arg,method_node) for arg in syntax.argument_nodes],"repository_relative_path":path,"source_occurrence_id":occ["occurrence_id"]})
                    publish=_kafka_publish_candidate(raw,syntax.syntax_node)
                    if publish is not None:
                        publish.update({"repository_relative_path":path,"source_occurrence_id":occ["occurrence_id"]})
                        kafka_publish_candidates.append(publish)
                    if syntax.method_name == "annotatedService" and len(syntax.argument_nodes) >= 2:
                        method_node=_ancestor(syntax.syntax_node,{"method_declaration"})
                        service_symbol=_text(raw,syntax.argument_nodes[1]).strip()
                        parameter_types=_parameter_types(raw,method_node) if method_node is not None else {}
                        service_type=parameter_types.get(service_symbol)
                        prefix_expression=_string_expression_fact(raw,syntax.argument_nodes[0],current_class=_class_context(raw,syntax.syntax_node)[0])
                        if service_type and prefix_expression is not None and service_symbol and all(ch.isalnum() or ch in "_$" for ch in service_symbol):
                            service_registrations.append({"service_symbol":service_symbol,"service_type":service_type,"prefix_expression":prefix_expression,"repository_relative_path":path,"source_occurrence_id":occ["occurrence_id"]})
            elif node.type == "object_creation_expression":
                syntax = object_creation_syntax_from_node(raw, node)
                if syntax is None: continue
                simple=syntax.syntactic_type.split("<",1)[0].rsplit(".",1)[-1]
                occ=_occurrence(repository_id=repository_id,path=path,sha=file_row.get("sha256"),node=syntax.syntax_node,basis="java_constructor_invocation"); occurrences[occ["occurrence_id"]]=occ
                events.append({"repository_relative_path":path,"source_occurrence_id":occ["occurrence_id"],"call_kind":"constructor_invocation","callable_name":simple,"receiver_identity":None,"receiver_basis":"constructor_type","argument_count":syntax.argument_count,"source_tree_scope":source_tree_scope(path)})

    grouped: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        key=(e["call_kind"],e["callable_name"],e["receiver_identity"],e["receiver_basis"],e["argument_count"])
        grouped[key].append(e)
    calls=[]
    for key, rows in grouped.items():
        kind,name,receiver,basis,arg_count=key
        occ_ids=sorted({r["source_occurrence_id"] for r in rows}); paths=sorted({r["repository_relative_path"] for r in rows}); scopes=sorted({r["source_tree_scope"] for r in rows})
        calls.append({"api_call_observation_id":stable_id("api_call_observation",repository_id,*key),"repository_id":repository_id,"language":"java","call_kind":kind,"callable_name":name,"receiver_identity":receiver,"receiver_basis":basis,"argument_count":arg_count,"occurrence_count":len(rows),"file_count":len(paths),"source_tree_scopes":scopes,"source_occurrence_ids":occ_ids,"claim":{"classification":"observed_fact","confidence":1.0,"basis":"tree_sitter_java_call_expression"},"probe":{"probe_id":"api_call_observations","probe_version":"2"}})
    imports.sort(key=lambda r:(r["repository_relative_path"],r["observation_kind"],r["namespace"],r["import_namespace_observation_id"]))
    annotations.sort(key=lambda r:(r["repository_relative_path"],r["annotation_name"],r["annotation_observation_id"]))
    calls.sort(key=lambda r:(r["call_kind"],r["callable_name"],str(r["receiver_identity"]),r["api_call_observation_id"]))
    diagnostics.sort(key=lambda r:(r["source_ref"]["repository_relative_path"],r["code"],r["diagnostic_id"]))
    if not java_files: status="not_applicable"; basis={"kind":"no_java_candidate_files"}
    elif skipped or failed: status="partial"; basis={"kind":"java_files_with_parser_gaps","candidate_file_count":len(java_files),"skipped_large":skipped,"failed":failed}
    else: status="complete"; basis={"kind":"tree_sitter_java_files_observed","candidate_file_count":len(java_files)}
    for call in calls:
        scopes = list(call.get("source_tree_scopes") or [])
        call.update(repository_local_salience(count=int(call.get("occurrence_count") or 1), file_count=int(call.get("file_count") or 1), source_tree_scopes=scopes, coverage_status=status))
    ps={k:{"status":status,"basis":basis} for k in ("import_namespace_observations","annotation_observations","api_call_observations")}
    property_bindings.sort(key=lambda row:(row["repository_relative_path"],row["symbol"],row["property_key"]))
    http_invocations.sort(key=lambda row:(row["repository_relative_path"],row["callable_name"],row["source_occurrence_id"]))
    wrapper_summaries.sort(key=lambda row:(row["callable_name"],row["argument_count"],row["path_parameter_index"],row["method"],row["repository_relative_path"]))
    jdk_http_calls.sort(key=lambda row:(row["repository_relative_path"],row["method"],row["request_symbol"],row["source_occurrence_id"]))
    string_constants.sort(key=lambda row:(row["owner"],row["name"],row["repository_relative_path"]))
    route_annotations.sort(key=lambda row:(row["class_name"],row["method"],row["repository_relative_path"],row["source_occurrence_id"]))
    service_registrations.sort(key=lambda row:(row["service_type"],row["service_symbol"],row["repository_relative_path"],row["source_occurrence_id"]))
    type_declarations.sort(key=lambda row:(row["type_name"],row["repository_relative_path"]))
    enum_constants.sort(key=lambda row:(row["owner"],row["name"],row["repository_relative_path"]))
    enum_metadata.sort(key=lambda row:(row["owner"],row["repository_relative_path"]))
    constructor_enum_refs.sort(key=lambda row:(row["class_name"],row["repository_relative_path"],row["source_occurrence_id"]))
    kafka_listeners.sort(key=lambda row:(row["class_name"],row["method_name"],row["repository_relative_path"],row["source_occurrence_id"]))
    kafka_publish_candidates.sort(key=lambda row:(row["class_name"],row["method_name"],row["repository_relative_path"],row["source_occurrence_id"]))
    http_boundary_observations.sort(key=lambda row:(row["repository_relative_path"],row["method"],row["path"],row["family_id"]))
    kafka_facts={"enum_constants":enum_constants,"enum_metadata":enum_metadata,"constructor_enum_refs":constructor_enum_refs,"listeners":kafka_listeners,"publish_candidates":kafka_publish_candidates,"type_declarations":type_declarations}
    return JavaProbeResult(imports,annotations,calls,sorted(occurrences.values(),key=lambda r:r["occurrence_id"]),diagnostics,ps,{"property_bindings":property_bindings,"invocations":http_invocations,"wrapper_summaries":wrapper_summaries,"jdk_http_calls":jdk_http_calls,"string_constants":string_constants,"route_annotations":route_annotations,"service_registrations":service_registrations,"type_declarations":type_declarations},kafka_facts,http_boundary_observations)
