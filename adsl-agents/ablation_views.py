"""Agent-only ablation views; originals and checker evidence remain unchanged."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path


_TRACE_KEYS = {'source_candidates', 'source_ids', 'source_locations', 'source_index',
               'source_index_path', 'feature_id', 'feature_ids', 'feature_path',
               'node_path', 'symbol', 'symbols', 'scope', 'scopes', 'allowed_scopes'}

def tracing_view(value):
    if isinstance(value, dict):
        return {k:tracing_view(v) for k,v in value.items()
                if k not in _TRACE_KEYS
                and not k.endswith(('feature_id','feature_ids','source_ids','source_locations'))
                and not k.endswith(('_ref', '_refs', '_pointer', '_path'))
                and k not in {'path', 'json_pointer', 'evidence_files', 'evidence_access',
                              'preview_info', 'evidence_path_instruction'}}
    if isinstance(value, list):
        return [tracing_view(v) for v in value]
    return deepcopy(value)


def record_hidden_tracing(payload, *, workspace, source_path, source_version, role):
    records = []
    def visit(value, path=''):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in _TRACE_KEYS:
                    records.append({'path':path+'/'+key, 'value':item})
                else:
                    visit(item, path+'/'+key)
        elif isinstance(value, list):
            for i,item in enumerate(value): visit(item,path+'/'+str(i))
    visit(payload)
    if not records: return
    data = {'source_version':source_version, 'role':role, 'hidden':records,
            'source_sha256':hashlib.sha256(Path(source_path).read_bytes()).hexdigest() if source_path else None}
    encoded = json.dumps(data, sort_keys=True, ensure_ascii=False)
    target = Path(workspace)/'ablation_private'/('tracing_'+hashlib.sha256(encoded.encode()).hexdigest()+'.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists(): target.write_text(encoded)


def sanitized_evidence(payload, *, workspace, context=None):
    data = tracing_view(payload)
    encoded = json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    target = Path(workspace)/'ablation_evidence'/('sanitized_'+digest+'.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists(): target.write_text(encoded)
    if context is not None:
        context.sanitized_evidence_paths.add(target.resolve())
    data['evidence_files'] = [{'path':target.relative_to(workspace).as_posix(), 'sha256':digest}]
    return data
