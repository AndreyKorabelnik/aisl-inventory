from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from io import BytesIO
import json
from typing import Any, Iterable
import xml.etree.ElementTree as ET

import yaml
from source_syntax_primitives import parse_proto_text

from .canonical import fingerprint, stable_id
from .landscape import repository_local_salience, source_tree_scope

_JSON_STRUCTURED_EXTENSIONS=frozenset({'.json','.avsc'})
_JSON_SOURCE_FORMAT_EXTENSIONS=frozenset({'.json'})
_YAML_EXTENSIONS=frozenset({'.yaml','.yml'})
_XML_EXTENSIONS=frozenset({'.xml','.xsd'})
_PROTO_EXTENSIONS=frozenset({'.proto'})
_STRUCTURED_EXTENSIONS=_JSON_STRUCTURED_EXTENSIONS|_YAML_EXTENSIONS
_SOURCE_FORMAT_EXTENSIONS=_JSON_SOURCE_FORMAT_EXTENSIONS|_YAML_EXTENSIONS|_XML_EXTENSIONS|_PROTO_EXTENSIONS
_RELEVANT_EXTENSIONS=_STRUCTURED_EXTENSIONS|_XML_EXTENSIONS|_PROTO_EXTENSIONS
_XSD_NAMESPACE='http://www.w3.org/2001/XMLSchema'
_OPENAPI_KEYS=('openapi','swagger')
_JSON_SCHEMA_PREFIXES=('https://json-schema.org/','http://json-schema.org/','https://json-schema.org/schema','http://json-schema.org/schema')

@dataclass(frozen=True)
class ProbeResult:
    source_formats:list[dict[str,Any]]
    structured_families:list[dict[str,Any]]
    structured_members:list[dict[str,Any]]
    xml_observations:list[dict[str,Any]]
    diagnostics:list[dict[str,Any]]
    probe_status:dict[str,dict[str,Any]]
    # Transient parser-owned material for repository-local HTTP projection. These rows
    # are not persisted as a second structured/config contract.
    scalar_property_values:dict[str,list[dict[str,Any]]]
    http_boundary_observations:list[dict[str,Any]]



_HTTP_METHODS=frozenset({'get','post','put','delete','patch','head','options','trace'})

def _scalar_property_values(doc:Any, *, repository_relative_path:str, source_occurrence_id:str)->dict[str,list[dict[str,Any]]]:
    """Return exact scalar configuration candidates without publishing a broad scalar surface.

    Nested mappings become dotted property keys. A dotted key that owns a descriptor
    object (for example ``foo.bar.path -> stringValue.default``) also contributes its
    exact ``default`` scalar. Lists are traversed without inventing index semantics.
    """
    out:dict[str,list[dict[str,Any]]]=defaultdict(list)

    def add(key:str,value:Any,basis:str)->None:
        if not key or not isinstance(value,(str,int,float,bool)) or isinstance(value,bool) and False:
            return
        row={'value':value,'repository_relative_path':repository_relative_path,'source_occurrence_id':source_occurrence_id,'basis':basis}
        if row not in out[key]:
            out[key].append(row)

    def descend_default(value:Any)->list[Any]:
        found=[]
        if isinstance(value,dict):
            for k,v in value.items():
                if str(k)=='default' and isinstance(v,(str,int,float,bool)):
                    found.append(v)
                elif isinstance(v,(dict,list)):
                    found.extend(descend_default(v))
        elif isinstance(value,list):
            for child in value:
                found.extend(descend_default(child))
        return found

    def walk(value:Any,path:tuple[str,...]=())->None:
        if isinstance(value,dict):
            for raw_key,child in value.items():
                key=str(raw_key)
                next_path=path+(key,)
                if isinstance(child,(str,int,float,bool)):
                    add('.'.join(next_path),child,'nested_structured_scalar')
                else:
                    if '.' in key:
                        for scalar in descend_default(child):
                            add(key,scalar,'dotted_config_key_default')
                    walk(child,next_path)
        elif isinstance(value,list):
            for child in value:
                walk(child,path)
    walk(doc)
    return {key:sorted(rows,key=lambda row:(str(row['value']),row['repository_relative_path'])) for key,rows in sorted(out.items())}



def _local_schema_payload(doc:dict[str,Any], schema:Any)->tuple[str|None,str|None,list[dict[str,Any]]]:
    if not isinstance(schema,dict):
        return None,None,[]
    ref=schema.get('$ref')
    target=schema
    identity=None
    if isinstance(ref,str) and ref.startswith('#/components/schemas/'):
        identity=ref.rsplit('/',1)[-1]
        components=doc.get('components') if isinstance(doc.get('components'),dict) else {}
        schemas=components.get('schemas') if isinstance(components.get('schemas'),dict) else {}
        target=schemas.get(identity)
        if not isinstance(target,dict):
            return identity,'unavailable_external_declaration',[]
    elif isinstance(schema.get('title'),str) and schema.get('title'):
        identity=str(schema.get('title'))
    if identity is None:
        return None,None,[]
    properties=target.get('properties') if isinstance(target,dict) else None
    if not isinstance(properties,dict):
        return identity,'available_local_declaration',[]
    required=set(target.get('required') or []) if isinstance(target.get('required'),list) else set()
    fields=[]
    for raw_name,prop in sorted(properties.items(),key=lambda item:str(item[0])):
        if not isinstance(raw_name,str) or not isinstance(prop,dict):
            continue
        collection='array' if prop.get('type')=='array' else 'scalar'
        declared_type=str(prop.get('type') or '')
        if not declared_type and isinstance(prop.get('$ref'),str):
            declared_type=prop['$ref'].rsplit('/',1)[-1]
        if collection=='array':
            items=prop.get('items') if isinstance(prop.get('items'),dict) else {}
            item_type=str(items.get('type') or '')
            if not item_type and isinstance(items.get('$ref'),str):
                item_type=items['$ref'].rsplit('/',1)[-1]
            declared_type=f'array<{item_type or "unknown"}>'
        fields.append({'name':raw_name,'declared_type':declared_type or 'unknown','collection':collection,'required':raw_name in required})
    return identity,'available_local_declaration',fields



def _resolve_openapi_local_component(doc:dict[str,Any], value:Any, section:str)->Any:
    if not isinstance(value,dict):
        return value
    ref=value.get('$ref')
    prefix=f'#/components/{section}/'
    if not isinstance(ref,str) or not ref.startswith(prefix):
        return value
    name=ref[len(prefix):]
    components=doc.get('components') if isinstance(doc.get('components'),dict) else {}
    values=components.get(section) if isinstance(components.get(section),dict) else {}
    resolved=values.get(name)
    return resolved if isinstance(resolved,dict) else value

def _openapi_operation_payload(doc:dict[str,Any], operation:dict[str,Any])->dict[str,Any]:
    out={}
    request_body=operation.get('requestBody') if isinstance(operation.get('requestBody'),dict) else None
    if request_body is not None:
        content=request_body.get('content') if isinstance(request_body.get('content'),dict) else {}
        media=content.get('application/json') if isinstance(content.get('application/json'),dict) else next((v for v in content.values() if isinstance(v,dict)),None)
        schema=media.get('schema') if isinstance(media,dict) else None
        identity,status,fields=_local_schema_payload(doc,schema)
        if identity:
            out.update({'request_payload':identity,'request_shape_status':status})
            if fields: out['request_fields']=fields
    responses=operation.get('responses') if isinstance(operation.get('responses'),dict) else {}
    successful=[]
    for code,row in responses.items():
        text=str(code)
        if text.startswith('2') and isinstance(row,dict):
            successful.append((text,row))
    if successful:
        _,row=sorted(successful,key=lambda item:item[0])[0]
        row=_resolve_openapi_local_component(doc,row,'responses')
        content=row.get('content') if isinstance(row.get('content'),dict) else {}
        media=content.get('application/json') if isinstance(content.get('application/json'),dict) else next((v for v in content.values() if isinstance(v,dict)),None)
        schema=media.get('schema') if isinstance(media,dict) else None
        identity,status,fields=_local_schema_payload(doc,schema)
        if identity:
            out.update({'response_payload':identity,'response_shape_status':status})
            if fields: out['response_fields']=fields
    return out

def _openapi_http_boundaries(*,repository_id:str,file_row:dict[str,Any],occurrence_id:str,doc:Any)->list[dict[str,Any]]:
    if _openapi(doc) is None or not isinstance(doc,dict):
        return []
    paths=doc.get('paths')
    if not isinstance(paths,dict):
        return []
    rows=[]
    for raw_path,path_item in sorted(paths.items(),key=lambda item:str(item[0])):
        if not isinstance(raw_path,str) or not isinstance(path_item,dict):
            continue
        for raw_method in sorted(path_item,key=lambda value:str(value)):
            method=str(raw_method).lower()
            if method not in _HTTP_METHODS:
                continue
            operation=path_item.get(raw_method)
            if not isinstance(operation,dict):
                continue
            payload_descriptor=_openapi_operation_payload(doc,operation)
            rows.append({
                'family_id':stable_id('http_boundary_observation',repository_id,'openapi',file_row['file_id'],method,raw_path),
                'repository_id':repository_id,
                'family_kind':'http_boundary_observation',
                'syntax_family':'openapi',
                'key':raw_path,
                'direction':'inbound',
                'protocol':'http',
                'method':method.upper(),
                'path':raw_path,
                'path_status':'resolved',
                'source_kind':'openapi_operation',
                'repository_relative_path':file_row['repository_relative_path'],
                'source_occurrence_id':occurrence_id,
                'occurrence_count':1,
                **payload_descriptor,
                'claim':{'classification':'observed_fact','confidence':1.0,'basis':'openapi_paths_operation'},
                'probe':{'probe_id':'http_boundary_observations','probe_version':'1'},
                'basis':{'kind':'openapi_paths_operation','semantic_meaning_inferred':False},
            })
    return rows

def _value_kind(v:Any)->str:
    if v is None:return 'null'
    if isinstance(v,bool):return 'boolean'
    if isinstance(v,(int,float)) and not isinstance(v,bool):return 'number'
    if isinstance(v,str):return 'string'
    if isinstance(v,dict):return 'mapping'
    if isinstance(v,list):return 'sequence'
    return 'unknown'

def _qname(name:str)->tuple[str|None,str]:
    if name.startswith('{') and '}' in name:
        ns,local=name[1:].split('}',1); return ns,local
    return None,name

def _diag(*,repository_id:str,path:str,occurrence_id:str|None,probe_id:str,code:str,message:str,basis:dict[str,Any])->dict[str,Any]:
    return {'diagnostic_id':stable_id('diagnostic',repository_id,path,probe_id,code,basis),'code':code,'severity':'warning','message':message,'source_ref':{'repository_relative_path':path,'localization_kind':'file'},'source_occurrence_id':occurrence_id,'basis':basis,'probe':{'probe_id':probe_id,'probe_version':'2'}}

def _format_row(*,repository_id:str,file_row:dict[str,Any],occurrence_id:str,source_format:str,version:str|None,classification:str,confidence:str,basis:dict[str,Any])->dict[str,Any]:
    return {'source_format_observation_id':stable_id('source_format_observation',repository_id,file_row['file_id'],source_format,version,classification,basis),'repository_id':repository_id,'file_id':file_row['file_id'],'repository_relative_path':file_row['repository_relative_path'],'source_occurrence_id':occurrence_id,'source_format':source_format,'format_version':version,'recognition_status':'confirmed','claim':{'classification':classification,'confidence':confidence,'basis':basis},'parse_status':'complete','probe':{'probe_id':'source_formats','probe_version':'2'}}

def _walk(v:Any,path:tuple[str,...]=())->Iterable[tuple[str,tuple[str,...],str,int]]:
    if isinstance(v,dict):
        for k in sorted(v,key=lambda x:str(x)):
            key=str(k); child=v[k]; p=path+(key,)
            yield key,p,_value_kind(child),len(path)
            yield from _walk(child,p)
    elif isinstance(v,list):
        p=path+('[]',)
        for child in v: yield from _walk(child,p)

def _aggregate(raw:list[tuple[str,tuple[str,...],str,int]])->list[dict[str,Any]]:
    by:dict[str,list[tuple[tuple[str,...],str,int]]]=defaultdict(list)
    for key,path,kind,depth in raw: by[key].append((path,kind,depth))
    out=[]
    for key,vals in sorted(by.items()):
        paths=sorted({v[0] for v in vals}); kinds=Counter(v[1] for v in vals); depths=[v[2] for v in vals]
        out.append({'key':key,'occurrence_count':len(vals),'path_count':len(paths),'single_path':list(paths[0]) if len(paths)==1 else None,'path_fingerprint':fingerprint([list(p) for p in paths]),'value_kinds':sorted(kinds),'value_kind_counts':{k:kinds[k] for k in sorted(kinds)},'depth_min':min(depths) if depths else None,'depth_max':max(depths) if depths else None,'localization_precision':'file_level','line_start':None,'line_end':None})
    return out

def _openapi(doc:Any):
    if not isinstance(doc,dict):return None
    present=[k for k in _OPENAPI_KEYS if k in doc]
    if len(present)!=1:return None
    val=doc[present[0]]
    if isinstance(val,(str,int,float)) and str(val).strip():return str(val).strip(),{'kind':'exact_top_level_marker','marker':present[0]}
    return None

def _json_schema(doc:Any):
    if not isinstance(doc,dict):return None
    uri=doc.get('$schema')
    if isinstance(uri,str) and any(uri.startswith(p) for p in _JSON_SCHEMA_PREFIXES):return ('observed_fact','high',uri,{'kind':'explicit_json_schema_uri','schema_uri':uri})
    markers=[]
    schema_type = doc.get('type')
    allowed_schema_types = {'object','array','string','number','integer','boolean','null'}
    if isinstance(schema_type, str) and schema_type in allowed_schema_types:
        markers.append('type')
    elif (
        isinstance(schema_type, list)
        and schema_type
        and all(isinstance(item, str) and item in allowed_schema_types for item in schema_type)
    ):
        markers.append('type')
    for m in ('properties','$defs','definitions','required','items','allOf','anyOf','oneOf','not','patternProperties'):
        if m in doc:markers.append(m)
    anchors={'properties','$defs','definitions','items','allOf','anyOf','oneOf','patternProperties'}
    if len(set(markers))>=2 and anchors.intersection(markers):return ('strongly_supported_inference','medium',None,{'kind':'sufficient_json_schema_structure','markers':sorted(set(markers))})
    ambiguous=sorted(set(doc).intersection({'type','properties','$defs','definitions','required','items'}))
    if ambiguous:return ('ambiguity','low',None,{'kind':'insufficient_json_schema_markers','markers':ambiguous})
    return None

def _xml_rows(repository_id:str,file_row:dict[str,Any],occurrence_id:str,payload:bytes)->tuple[list[dict[str,Any]],ET.Element,list[tuple[str,str]]]:
    ns=[]
    for event,item in ET.iterparse(BytesIO(payload),events=('start-ns',)):
        prefix,uri=item; ns.append((prefix or '',uri))
    root=ET.fromstring(payload)

    # ElementTree gives us exact parsed XML structure but no stable source span for
    # each element/attribute.  Repeated identical structural observations in one
    # file therefore cannot honestly receive distinct localized identities.
    # Aggregate them by their parser-owned structural key and preserve frequency.
    counts:Counter[tuple[str,str|None,str|None,str|None]]=Counter()
    def observe(kind:str,*,namespace_uri:str|None,local_name:str|None,prefix:str|None):
        counts[(kind,namespace_uri,local_name,prefix)] += 1

    root_ns,root_local=_qname(root.tag)
    observe('document_root',namespace_uri=root_ns,local_name=root_local,prefix=None)
    for prefix,uri in sorted(set(ns)):
        observe('namespace',namespace_uri=uri,local_name=None,prefix=prefix)
    for el in root.iter():
        ens,local=_qname(el.tag)
        observe('element',namespace_uri=ens,local_name=local,prefix=None)
        for attr in sorted(el.attrib):
            ans,alocal=_qname(attr)
            observe('attribute',namespace_uri=ans,local_name=alocal,prefix=None)

    rows=[]
    for (kind,namespace_uri,local_name,prefix),occurrence_count in sorted(
        counts.items(),
        key=lambda item:(item[0][0],str(item[0][1]),str(item[0][2]),str(item[0][3])),
    ):
        material=(kind,{'namespace_uri':namespace_uri,'local_name':local_name,'prefix':prefix})
        rows.append({
            'xml_observation_id':stable_id('xml_observation',repository_id,file_row['file_id'],material),
            'repository_id':repository_id,
            'file_id':file_row['file_id'],
            'repository_relative_path':file_row['repository_relative_path'],
            'source_occurrence_id':occurrence_id,
            'observation_kind':kind,
            'namespace_uri':namespace_uri,
            'local_name':local_name,
            'prefix':prefix,
            'occurrence_count':occurrence_count,
            'localization_precision':'file_level',
            'claim':{'classification':'observed_fact','confidence':1.0,'basis':'xml_parser_structure_file_local_aggregation'},
            'probe':{'probe_id':'xml_observations','probe_version':'2'},
        })
    return rows,root,ns

def _families(repository_id:str,docs:list[dict[str,Any]],coverage_status:str)->list[dict[str,Any]]:
    families=[]

    # A document-shape family is repository-level. Multiple files with the same
    # parser-owned shape share one family identity and are aggregated rather than
    # emitted as duplicate rows with the same family_id.
    shape_groups:dict[tuple[str,str,str],list[dict[str,Any]]]=defaultdict(list)
    for doc in docs:
        sig=fingerprint({'syntax_family':doc['syntax_family'],'root_kind':doc['root_kind'],'members':[(m['key'],m['path_fingerprint'],m['value_kinds']) for m in doc['members']]})
        shape_groups[(doc['syntax_family'],doc['root_kind'],sig)].append(doc)
    for (syntax,root_kind,sig),group in sorted(shape_groups.items()):
        fid=stable_id('structured_document_shape',repository_id,syntax,sig)
        file_ids=sorted({doc['file_id'] for doc in group})
        occurrence_ids=sorted({doc['source_occurrence_id'] for doc in group})
        scopes=sorted({doc['source_tree_scope'] for doc in group})
        families.append({
            'family_id':fid,
            'repository_id':repository_id,
            'family_kind':'structured_document_shape',
            'syntax_family':syntax,
            'key':None,
            'root_kind':root_kind,
            'structural_signature':sig,
            'document_count':len(group),
            'file_count':len(file_ids),
            'key_family_member_count':sum(len(doc['members']) for doc in group),
            'key_occurrence_count':sum(m['occurrence_count'] for doc in group for m in doc['members']),
            'distinct_path_count':sum(m['path_count'] for doc in group for m in doc['members']),
            'file_ids':file_ids,
            'source_occurrence_ids':occurrence_ids,
            'source_tree_scope_count':len(scopes),
            'source_tree_scopes':scopes,
            **repository_local_salience(count=len(group),file_count=len(file_ids),source_tree_scopes=scopes,coverage_status=coverage_status),
            'basis':{'kind':'parser_owned_file_local_key_path_shape'},
            'probe':{'probe_id':'structured_families','probe_version':'2'},
        })

    grouped:dict[tuple[str,str],list[tuple[dict[str,Any],dict[str,Any]]]]=defaultdict(list)
    for doc in docs:
        for m in doc['members']:grouped[(doc['syntax_family'],m['key'])].append((doc,m))
    for (syntax,key),obs in sorted(grouped.items()):
        kinds=Counter(); depths=[]
        for _,m in obs:
            kinds.update(m['value_kind_counts']);
            if m['depth_min'] is not None:depths += [m['depth_min'],m['depth_max']]
        fid=stable_id('structured_key_family',repository_id,syntax,key)
        count=sum(m['occurrence_count'] for _,m in obs)
        file_ids=sorted({d['file_id'] for d,_ in obs})
        occurrence_ids=sorted({d['source_occurrence_id'] for d,_ in obs})
        scopes=sorted({d['source_tree_scope'] for d,_ in obs})
        families.append({'family_id':fid,'repository_id':repository_id,'family_kind':'structured_key_family','syntax_family':syntax,'key':key,'structural_signature':fingerprint({'syntax_family':syntax,'key':key,'value_kinds':sorted(kinds)}),'occurrence_count':count,'file_count':len(file_ids),'member_count':len(obs),'file_local_path_count_sum':sum(m['path_count'] for _,m in obs),'value_kinds':sorted(kinds),'value_kind_counts':{k:kinds[k] for k in sorted(kinds)},'depth_min':min(depths) if depths else None,'depth_max':max(depths) if depths else None,'file_ids':file_ids,'source_occurrence_ids':occurrence_ids,'source_tree_scope_count':len(scopes),'source_tree_scopes':scopes,**repository_local_salience(count=count,file_count=len(file_ids),source_tree_scopes=scopes,coverage_status=coverage_status),'basis':{'kind':'repository_local_exact_key_family_aggregation','semantic_meaning_inferred':False},'probe':{'probe_id':'structured_families','probe_version':'2'}})
    return sorted(families,key=lambda r:(r['family_kind'],r['syntax_family'],r.get('key') or '',r['family_id']))

def _status(applicable:int,complete:int,partial:int,failed:int,basis_kind:str):
    if applicable==0:s='not_applicable'
    elif failed==applicable:s='failed'
    elif failed or partial:s='partial'
    else:s='complete'
    return {'status':s,'basis':{'kind':basis_kind,'applicable_file_count':applicable,'complete_file_count':complete,'partial_file_count':partial,'failed_file_count':failed}}

def run_i3a_probes(*,repository_id:str,files:list[dict[str,Any]],source_bytes_by_path:dict[str,bytes],occurrence_by_path:dict[str,str],max_probe_file_bytes:int)->ProbeResult:
    source_formats=[]; structured_members=[]; xml_observations=[]; diagnostics=[]; docs=[]
    scalar_property_values:dict[str,list[dict[str,Any]]]=defaultdict(list)
    http_boundary_observations:list[dict[str,Any]]=[]
    sf_a=sf_c=sf_p=sf_f=st_a=st_c=st_p=st_f=xml_a=xml_c=xml_p=xml_f=0
    for file_row in files:
        ext=file_row['extension']; path=file_row['repository_relative_path']
        if ext not in _RELEVANT_EXTENSIONS:continue
        occ=occurrence_by_path[path]; payload=source_bytes_by_path.get(path)
        if ext in _SOURCE_FORMAT_EXTENSIONS:sf_a+=1
        if ext in _STRUCTURED_EXTENSIONS:st_a+=1
        if ext in _XML_EXTENSIONS:xml_a+=1
        if payload is None:
            if ext in _SOURCE_FORMAT_EXTENSIONS:sf_f+=1
            if ext in _STRUCTURED_EXTENSIONS:st_f+=1
            if ext in _XML_EXTENSIONS:xml_f+=1
            continue
        if len(payload)>max_probe_file_bytes:
            diagnostics.append(_diag(repository_id=repository_id,path=path,occurrence_id=occ,probe_id='source_formats',code='inventory_probe_file_size_limit',message='Structured/source-format parser skipped oversized file.',basis={'byte_size':len(payload),'max_probe_file_bytes':max_probe_file_bytes,'extension':ext}))
            if ext in _SOURCE_FORMAT_EXTENSIONS:sf_p+=1
            if ext in _STRUCTURED_EXTENSIONS:st_p+=1
            if ext in _XML_EXTENSIONS:xml_p+=1
            continue
        if ext in _STRUCTURED_EXTENSIONS:
            try:
                if ext in _JSON_STRUCTURED_EXTENSIONS: doc=json.loads(payload); syntax='json'
                else: doc=yaml.safe_load(payload.decode('utf-8-sig')); syntax='yaml'
            except Exception as exc:
                if ext in _SOURCE_FORMAT_EXTENSIONS:sf_p+=1
                st_f+=1
                code='structured_json_parse_failed' if ext in _JSON_STRUCTURED_EXTENSIONS else 'structured_yaml_scan_partial'
                diagnostics.append(_diag(repository_id=repository_id,path=path,occurrence_id=occ,probe_id='structured_families',code=code,message='Parser-owned structured observation failed; no semantic absence is inferred.',basis={'error_type':type(exc).__name__,'message':str(exc)[:240]}))
                continue
            if ext in _SOURCE_FORMAT_EXTENSIONS:sf_c+=1
            st_c+=1
            members=_aggregate(list(_walk(doc)))
            docs.append({'syntax_family':syntax,'root_kind':_value_kind(doc),'file_id':file_row['file_id'],'repository_relative_path':path,'source_occurrence_id':occ,'source_tree_scope':source_tree_scope(path),'members':members})
            for property_key, rows in _scalar_property_values(doc, repository_relative_path=path, source_occurrence_id=occ).items():
                scalar_property_values[property_key].extend(rows)
            http_boundary_observations.extend(_openapi_http_boundaries(repository_id=repository_id,file_row=file_row,occurrence_id=occ,doc=doc))
            for m in members:
                structured_members.append({'member_id':stable_id('structured_member',repository_id,file_row['file_id'],syntax,m['key']),'family_id':stable_id('structured_key_family',repository_id,syntax,m['key']),'repository_id':repository_id,'file_id':file_row['file_id'],'repository_relative_path':path,'source_occurrence_id':occ,'member_kind':'structured_key_family_member','syntax_family':syntax,**m,'basis':{'kind':f'successful_{syntax}_parse_and_file_local_key_aggregation'},'probe':{'probe_id':'structured_families','probe_version':'2'}})
            if ext in _JSON_SOURCE_FORMAT_EXTENSIONS or ext in _YAML_EXTENSIONS:
                oa=_openapi(doc)
                if oa:
                    ver,basis=oa;source_formats.append(_format_row(repository_id=repository_id,file_row=file_row,occurrence_id=occ,source_format='openapi',version=ver,classification='observed_fact',confidence='high',basis=basis))
                js=_json_schema(doc)
                if js:
                    classification,confidence,version,basis=js
                    if classification=='ambiguity':
                        diagnostics.append(_diag(repository_id=repository_id,path=path,occurrence_id=occ,probe_id='source_formats',code='json_schema_markers_ambiguous',message='JSON Schema-like markers were insufficient for confirmation.',basis=basis))
                    else:source_formats.append(_format_row(repository_id=repository_id,file_row=file_row,occurrence_id=occ,source_format='json_schema',version=version,classification=classification,confidence=confidence,basis=basis))
        elif ext in _PROTO_EXTENSIONS:
            try:
                text=payload.decode('utf-8-sig'); result=parse_proto_text(text)
                if not result.parsed or result.document is None:
                    if result.failure_message:
                        raise ValueError(result.failure_message)
                    message='; '.join(item.message for item in result.diagnostics) or result.parser_status
                    raise ValueError(message)
                syntax=result.document.syntax; element_count=result.document.non_comment_element_count
            except Exception as exc:
                sf_p+=1
                diagnostics.append(_diag(repository_id=repository_id,path=path,occurrence_id=occ,probe_id='source_formats',code='proto_parse_failed',message='Protocol Buffers parser failed; format was not confirmed.',basis={'error_type':type(exc).__name__,'message':str(exc)[:240]}))
                continue
            sf_c+=1
            if syntax in {'proto2','proto3'} and element_count:
                source_formats.append(_format_row(repository_id=repository_id,file_row=file_row,occurrence_id=occ,source_format='protobuf',version=syntax,classification='observed_fact',confidence='high',basis={'kind':'proto_schema_parser_ast','syntax':syntax,'declaration_count':element_count}))
            else:
                diagnostics.append(_diag(repository_id=repository_id,path=path,occurrence_id=occ,probe_id='source_formats',code='proto_format_not_confirmed',message='A .proto file was observed but structured syntax/declaration evidence was insufficient.',basis={'syntax':syntax,'non_comment_element_count':element_count}))
        elif ext in _XML_EXTENSIONS:
            try: rows,root,nss=_xml_rows(repository_id,file_row,occ,payload)
            except Exception as exc:
                xml_f+=1; sf_p+=1
                diagnostics.append(_diag(repository_id=repository_id,path=path,occurrence_id=occ,probe_id='xml_observations',code='structured_xml_parse_failed',message='XML parser failed; no XML/XSD facts were emitted.',basis={'error_type':type(exc).__name__,'message':str(exc)[:240]}));continue
            xml_c+=1; sf_c+=1; xml_observations.extend(rows)
            ns,local=_qname(root.tag)
            if ext=='.xsd':
                if ns==_XSD_NAMESPACE and local=='schema':source_formats.append(_format_row(repository_id=repository_id,file_row=file_row,occurrence_id=occ,source_format='xsd',version=None,classification='observed_fact',confidence='high',basis={'kind':'xml_schema_root_namespace','namespace_uri':ns,'local_name':local}))
                else:diagnostics.append(_diag(repository_id=repository_id,path=path,occurrence_id=occ,probe_id='source_formats',code='xsd_format_not_confirmed',message='The .xsd suffix was not sufficient; XML Schema root/namespace evidence was absent.',basis={'namespace_uri':ns,'local_name':local}))
    structured_members.sort(key=lambda r:(r['repository_relative_path'],r['syntax_family'],r['key'],r['member_id']))
    source_formats.sort(key=lambda r:(r['repository_relative_path'],r['source_format'],r['source_format_observation_id']))
    xml_observations.sort(key=lambda r:(r['repository_relative_path'],r['observation_kind'],str(r.get('namespace_uri')),str(r.get('local_name')),r['xml_observation_id']))
    diagnostics.sort(key=lambda r:(r['source_ref']['repository_relative_path'],r['code'],r['diagnostic_id']))
    source_format_status=_status(sf_a,sf_c,sf_p,sf_f,'parser_owned_source_format_observations')
    structured_status=_status(st_a,st_c,st_p,st_f,'parser_owned_structured_observations')
    xml_status=_status(xml_a,xml_c,xml_p,xml_f,'elementtree_xml_observations')
    scalar_property_values={key:sorted({(str(row['value']),row['repository_relative_path'],row['basis']):row for row in rows}.values(),key=lambda row:(str(row['value']),row['repository_relative_path'])) for key,rows in sorted(scalar_property_values.items())}
    http_boundary_observations.sort(key=lambda row:(row['repository_relative_path'],row['method'],row['path'],row['family_id']))
    return ProbeResult(source_formats,_families(repository_id,docs,structured_status['status']),structured_members,xml_observations,diagnostics,{'source_formats':source_format_status,'structured_families':structured_status,'xml_observations':xml_status},scalar_property_values,http_boundary_observations)
