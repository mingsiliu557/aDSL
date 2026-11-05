"""Output-only tracing redaction. Original references remain internal."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

_TRACE_KEYS = {'source_candidates','source_ids','source_locations','source_index',
               'source_index_ref','source_index_path','feature_id','feature_ids',
               'feature_path','symbol','symbols','scopes','allowed_scopes','bridge_feature_id'}
_DIAGNOSTICS = {'execution_failure','source_execution_error','execution_error','execution_failure_summary'}

def _diagnostic(row):
    return isinstance(row,dict) and (row.get('code') == 'GENERATED_SOURCE_EXECUTION_FAILED'
        or row.get('stage') in {'source_execution','source_contract'}
        or row.get('failure_kind') in {'source_execution','source_contract'})

def _physical_rows(value, physical=False):
    if isinstance(value,dict):
        if _diagnostic(value): return
        physical = physical or 'finding_id' in value or 'source_candidates' in value or value.get('checker','').startswith('assembly_')
        if physical: yield value; return
        for key,item in value.items():
            if key not in _DIAGNOSTICS:
                yield from _physical_rows(item, key in {'support_evidence','support','typed_findings','findings','source_candidates'})
    elif isinstance(value,list):
        for row in value: yield from _physical_rows(row,physical)


def tracing_view(value, *, physical=False):
    if isinstance(value,dict):
        if _diagnostic(value): return deepcopy(value)
        physical = physical or 'finding_id' in value or 'source_candidates' in value or str(value.get('checker','')).startswith('assembly_')
        return {k:(deepcopy(v) if k in _DIAGNOSTICS else tracing_view(v,physical=physical or k in {'support_evidence','support','typed_findings','findings'}))
                for k,v in value.items() if k not in {'source_index','source_index_ref','source_index_path'}
                and not (physical and k in _TRACE_KEYS)}
    if isinstance(value,list):return [tracing_view(v,physical=physical) for v in value]
    return deepcopy(value)


def record_hidden_tracing(payload, *, workspace, source_path, source_version, role):
    records = []
    def visit(value, path=''):
        if isinstance(value, dict):
            if _diagnostic(value):return
            for key, item in value.items():
                if key in _DIAGNOSTICS:continue
                if key in _TRACE_KEYS:
                    records.append({'path':path+'/'+key, 'value':item})
                else:
                    visit(item, path+'/'+key)
        elif isinstance(value, list):
            for i,item in enumerate(value): visit(item,path+'/'+str(i))
    for physical in _physical_rows(payload):
        visit(physical)
    if not records: return
    data = {'source_version':source_version, 'role':role, 'hidden':records,
            'source_sha256':hashlib.sha256(Path(source_path).read_bytes()).hexdigest() if source_path else None}
    encoded = json.dumps(data, sort_keys=True, ensure_ascii=False)
    target = Path(workspace)/'ablation_private'/('tracing_'+hashlib.sha256(encoded.encode()).hexdigest()+'.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists(): target.write_text(encoded)



def _finite_view(value):
    """Mark the nearest containing mapping without altering list positions."""
    import math
    def convert(item):
        if isinstance(item, float) and not math.isfinite(item):
            return None, True
        if isinstance(item, list):
            rows = [convert(v) for v in item]
            return [v for v,flag in rows], any(flag for v,flag in rows)
        if isinstance(item, dict):
            rows = {k:convert(v) for k,v in item.items()}
            result = {k:v for k,(v,flag) in rows.items()}
            if any(flag for v,flag in rows.values()): result.setdefault('value_status','NONFINITE')
            return result, False
        return deepcopy(item), False
    return convert(value)[0]


def sanitized_evidence(payload, *, workspace, source_path=None, context=None, write=False):
    """Plan references entirely in memory; commit files only for final output."""
    workspace=Path(workspace).resolve()
    source=Path(source_path).resolve() if source_path else None
    pending={}; cache={}; active=set(); registered=set()
    def hidden(ref):
        return {**ref,'path':None,'availability':'UNAVAILABLE','reason':'hidden_by_ablation'}
    def pointer_ok(doc,pointer):
        try:
            for token in pointer.split('/')[1:]:
                token=token.replace('~1','/').replace('~0','~')
                doc=doc[int(token)] if isinstance(doc,list) else doc[token]
            return True
        except (KeyError,ValueError,TypeError,IndexError): return False
    def visit(value):
        if isinstance(value,list): return [visit(v) for v in value]
        if not isinstance(value,dict): return deepcopy(value)
        result={k:visit(v) for k,v in value.items() if k!='path'}
        if 'path' not in value:return result
        path=value['path']
        if not path:result['path']=path;return result
        target=Path(path)
        if not target.is_absolute():target=workspace/target
        target=target.resolve()
        if target==source:result['path']=path;return result
        if (not target.is_relative_to(workspace) or not target.is_file()
                or target.suffix!='.json' or target.name in {'source_index.json','assembly_versions.json','run.json','runtime_config.json','checkpoint.json','book.json','assembly_result.json','decision.json','reviews.json','repair_history.json'}
                or 'ablation_private' in target.relative_to(workspace).parts):return hidden(result)
        if target.parent==workspace/'ablation_evidence' and target.name.startswith('sanitized_'):
            # Validate content-address identity, including nested references.
            raw=target.read_bytes()
            if target.stem!='sanitized_'+hashlib.sha256(raw).hexdigest():return hidden(result)
            registered.add(target)
            doc=json.loads(raw)
            for row in paths(doc):
                other=(workspace/row['path']).resolve()
                if other!=source:
                    if other.parent!=workspace/'ablation_evidence' or not other.is_file():return hidden(result)
                    validated=visit(row)
                    if not validated.get('path'):return hidden(result)
                    registered.add(other)
            result['path']=target.relative_to(workspace).as_posix()
            result['file_sha256']=hashlib.sha256(raw).hexdigest()
        else:
            raw=target.read_bytes();key=(str(target),hashlib.sha256(raw).hexdigest())
            if key in active:return hidden(result)
            if key not in cache:
                active.add(key)
                try:doc=_finite_view(visit(tracing_view(json.loads(raw))))
                except (ValueError,UnicodeError):active.remove(key);return hidden(result)
                active.remove(key)
                text=json.dumps(doc,sort_keys=True,ensure_ascii=False,allow_nan=False)
                digest=hashlib.sha256(text.encode()).hexdigest()
                dest=workspace/'ablation_evidence'/('sanitized_'+digest+'.json')
                pending[dest]=text;cache[key]=(dest,digest,doc)
            target,digest,doc=cache[key];registered.add(target)
            result['path']=target.relative_to(workspace).as_posix();result['file_sha256']=digest
        if value.get('json_pointer') and not pointer_ok(doc,value['json_pointer']):
            result.update(availability='UNAVAILABLE',reason='hidden_by_ablation')
        return result
    def paths(value):
        if isinstance(value,dict):
            if value.get('path'):yield value
            for item in value.values():yield from paths(item)
        elif isinstance(value,list):
            for item in value:yield from paths(item)
    out=_finite_view(tracing_view(visit(payload)))
    if write:
        for path,text in pending.items():
            path.parent.mkdir(parents=True,exist_ok=True)
            if not path.exists():path.write_text(text)
        if context is not None:context.sanitized_evidence_paths.update(registered)
    return out
